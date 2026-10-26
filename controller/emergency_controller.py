"""
emergency_controller.py  --  Ryu OpenFlow 1.3 controller (simple version)

Run:  ryu-manager controller/emergency_controller.py
      SDN_MODE=static ryu-manager ...        (default mode: dynamic)

WHAT IT DOES
  1. ROUTING      Every switch forwards by destination IP (flow priority 10).
  2. PRIORITY     UDP traffic with source OR destination port 5000 is "emergency".
                  It gets its own rule (priority 100) that puts packets in queue 1,
                  the high-priority queue configured on the switch-to-switch ports.
                  iperf (port 5001) does not match, so it stays best-effort.
  3. APP -> SDN   The server sends small JSON events to the virtual IP 10.0.0.100,
                  UDP port 9998. Switch s1 sends them to this controller (packet-in).
                     {"type":"alert_start"}   -> reroute normal traffic away from the
                                                 direct link BEFORE congestion builds
                     {"type":"mode","value":"static|priority|dynamic"}
                  Other event types (register, alert_end, ...) are only logged.
  4. DYNAMIC      (dynamic mode only) every 2 s we read the byte counter of the
                  bottleneck port (s1 towards s3). At 7 Mbps or more we move normal
                  traffic onto the alternate path s1-s2-s3 for 15 s.

MODES
  static    BASELINE: routing only. No emergency rule, no reaction to anything.
  priority  routing + emergency rule (queue 1).
  dynamic   priority + congestion / alert-triggered rerouting.
"""
import json
import os
import sys
import time

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.lib import hub
from ryu.lib.packet import packet, udp
from ryu.ofproto import ofproto_v1_3

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import network_config as cfg  # noqa: E402

ETH_IP, IP_UDP = 0x0800, 17           # match values for "IPv4" and "UDP"
PRIO_BEST, PRIO_EMERG, PRIO_EVENT = 10, 100, 200   # flow priorities (higher wins)


class EmergencyController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dps = {}                    # dpid -> datapath (switch connection)
        self.mode = os.environ.get('SDN_MODE', 'dynamic')
        if self.mode not in cfg.MODES:
            self.mode = 'dynamic'
        self.rerouted = False            # True while normal traffic uses the alternate path
        self.reroute_until = 0.0         # time until which the reroute is kept
        self.last_tx = None              # (tx_bytes, time) from the previous poll
        self.logger.info('controller started, mode=%s', self.mode)
        hub.spawn(self._monitor)         # background task: poll port statistics

    # ------------------------------------------------------------------
    # 1. A switch connects -> install all its rules
    # ------------------------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def on_switch_connect(self, ev):
        dp = ev.msg.datapath
        self.dps[dp.id] = dp
        self.logger.info('switch s%s connected', dp.id)
        self.install_all(dp)

    def install_all(self, dp):
        self.delete_all_flows(dp)
        self.install_event_rule(dp)
        self.install_best_effort(dp)
        self.install_emergency(dp)

    # ------------------------------------------------------------------
    # Small helpers for talking OpenFlow
    # ------------------------------------------------------------------
    def add_flow(self, dp, priority, match, actions):
        """Install (or overwrite) one flow rule."""
        ofp, parser = dp.ofproto, dp.ofproto_parser
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=priority,
                                      match=match, instructions=inst))

    def delete_all_flows(self, dp):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        dp.send_msg(parser.OFPFlowMod(datapath=dp, table_id=ofp.OFPTT_ALL,
                                      command=ofp.OFPFC_DELETE,
                                      out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY,
                                      match=parser.OFPMatch()))

    def out_port(self, dp, dst_ip, use_alt):
        """Output port on switch dp for a packet going to dst_ip."""
        dst_sw, host_port = cfg.HOST_BY_IP[dst_ip]
        if dp.id == dst_sw:                       # destination host is on this switch
            return host_port
        table = cfg.NEXT_PORT_ALT if use_alt else cfg.NEXT_PORT_DIRECT
        return table[dp.id][dst_sw]               # otherwise go towards its switch

    # ------------------------------------------------------------------
    # Rule type 1: plain routing, priority 10 (all traffic, normal queue)
    # ------------------------------------------------------------------
    def install_best_effort(self, dp):
        parser = dp.ofproto_parser
        for ip in cfg.HOST_BY_IP:
            match = parser.OFPMatch(eth_type=ETH_IP, ipv4_dst=ip)
            actions = [parser.OFPActionOutput(self.out_port(dp, ip, self.rerouted))]
            self.add_flow(dp, PRIO_BEST, match, actions)

    # ------------------------------------------------------------------
    # Rule type 2: emergency traffic, priority 100 (queue 1, always direct path)
    # ------------------------------------------------------------------
    def install_emergency(self, dp):
        if self.mode == 'static':                 # baseline: no special treatment
            return
        parser = dp.ofproto_parser
        for ip in cfg.HOST_BY_IP:
            port = self.out_port(dp, ip, False)
            actions = [parser.OFPActionOutput(port)]
            if cfg.HOST_BY_IP[ip][0] != dp.id:    # leaving towards another switch:
                actions.insert(0, parser.OFPActionSetQueue(cfg.Q_EMERGENCY))  # use queue 1
            # OpenFlow cannot say "source OR destination port", so one rule for each
            for field in ('udp_src', 'udp_dst'):
                match = parser.OFPMatch(eth_type=ETH_IP, ip_proto=IP_UDP, ipv4_dst=ip,
                                        **{field: cfg.SERVER_PORT})
                self.add_flow(dp, PRIO_EMERG, match, actions)

    # ------------------------------------------------------------------
    # Rule type 3: app event channel, priority 200, only on the server's switch
    # ------------------------------------------------------------------
    def install_event_rule(self, dp):
        if dp.id != cfg.HOST_BY_IP[cfg.SERVER_IP][0]:
            return
        ofp, parser = dp.ofproto, dp.ofproto_parser
        match = parser.OFPMatch(eth_type=ETH_IP, ip_proto=IP_UDP, udp_dst=cfg.CTL_PORT)
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        self.add_flow(dp, PRIO_EVENT, match, actions)

    # ------------------------------------------------------------------
    # 3. APP -> SDN: events arrive as packet-in messages
    # ------------------------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def on_packet_in(self, ev):
        pkt = packet.Packet(ev.msg.data)
        u = pkt.get_protocol(udp.udp)
        if u is None or u.dst_port != cfg.CTL_PORT:
            return
        try:
            event = json.loads(bytes(pkt.protocols[-1]).decode('utf-8'))
        except (ValueError, TypeError):
            self.logger.warning('bad event datagram')
            return
        self.handle_event(event)

    def handle_event(self, event):
        kind = event.get('type')
        self.logger.info('APP EVENT: %s', event)
        if kind == 'alert_start':
            # an alert burst is starting: move normal traffic off the direct link now
            self.reroute_until = time.time() + cfg.REROUTE_HOLD
            self.update_reroute('alert_start event')
        elif kind == 'mode' and event.get('value') in cfg.MODES:
            self.mode = event['value']
            self.rerouted = False
            self.reroute_until = 0.0
            self.logger.info('MODE -> %s', self.mode)
            for dp in self.dps.values():
                self.install_all(dp)

    # ------------------------------------------------------------------
    # 4. DYNAMIC behaviour: reroute normal traffic via s2 while "reroute_until" is in the future
    # ------------------------------------------------------------------
    def update_reroute(self, reason):
        want = self.mode == 'dynamic' and time.time() < self.reroute_until
        if want == self.rerouted:
            return                                # nothing to change
        self.rerouted = want
        self.logger.info('REROUTE normal traffic %s the direct link (%s)',
                         'OFF' if want else 'back ONTO', reason)
        for dp in self.dps.values():
            self.install_best_effort(dp)          # overwrites the priority-10 rules

    def _monitor(self):
        """Every POLL_INTERVAL seconds: ask s1 for port counters, and check the hold timer."""
        while True:
            hub.sleep(cfg.POLL_INTERVAL)
            dp = self.dps.get(cfg.BOTTLE_DPID)
            if dp:
                parser = dp.ofproto_parser
                dp.send_msg(parser.OFPPortStatsRequest(dp, 0, dp.ofproto.OFPP_ANY))
            self.update_reroute('hold timer')     # releases the reroute when time is up

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def on_port_stats(self, ev):
        if ev.msg.datapath.id != cfg.BOTTLE_DPID:
            return
        now = time.time()
        for st in ev.msg.body:
            if st.port_no != cfg.BOTTLE_PORT:
                continue
            if self.last_tx is not None:
                # rate = bytes sent since last poll, in Mbps
                mbps = (st.tx_bytes - self.last_tx[0]) * 8 / (now - self.last_tx[1]) / 1e6
                self.logger.info('bottleneck s%d port %d: %.2f Mbps (mode=%s, rerouted=%s)',
                                 cfg.BOTTLE_DPID, cfg.BOTTLE_PORT, mbps, self.mode, self.rerouted)
                if self.mode == 'dynamic' and mbps >= cfg.CONGEST_MBPS:
                    self.reroute_until = now + cfg.REROUTE_HOLD
                    self.update_reroute('congestion')
            self.last_tx = (st.tx_bytes, now)
