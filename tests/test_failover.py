import sys, types, os
def mod(name, **kw):
    m = types.ModuleType(name); m.__dict__.update(kw); sys.modules[name] = m; return m
class RyuApp:
    def __init__(self, *a, **k):
        import logging; self.logger = logging.getLogger('t'); logging.basicConfig(level=logging.INFO, format='  ctl: %(message)s')
deco = lambda *a, **k: (lambda f: f)
ryu = mod('ryu'); mod('ryu.base'); mod('ryu.controller'); mod('ryu.lib'); mod('ryu.lib.packet'); mod('ryu.ofproto')
mod('ryu.base.app_manager', RyuApp=RyuApp); ryu.base = sys.modules['ryu.base']; sys.modules['ryu.base'].app_manager = sys.modules['ryu.base.app_manager']
mod('ryu.controller.ofp_event', **{n: 0 for n in ['EventOFPSwitchFeatures','EventOFPPacketIn','EventOFPPortStatsReply','EventOFPPortStatus','EventOFPPortDescStatsReply']})
mod('ryu.controller.handler', CONFIG_DISPATCHER=1, MAIN_DISPATCHER=2, set_ev_cls=deco)
sys.modules['ryu.controller'].ofp_event = sys.modules['ryu.controller.ofp_event']
mod('ryu.lib.hub', spawn=lambda *a, **k: None, sleep=lambda s: None); sys.modules['ryu.lib'].hub = sys.modules['ryu.lib.hub']
mod('ryu.lib.packet.packet', Packet=object); mod('ryu.lib.packet.udp', udp=object)
sys.modules['ryu.lib.packet'].packet = sys.modules['ryu.lib.packet.packet']; sys.modules['ryu.lib.packet'].udp = sys.modules['ryu.lib.packet.udp']
mod('ryu.ofproto.ofproto_v1_3', OFP_VERSION=4); sys.modules['ryu.ofproto'].ofproto_v1_3 = sys.modules['ryu.ofproto.ofproto_v1_3']
sys.path.insert(0, 'controller'); sys.path.insert(0, 'src')
import emergency_controller as ec

class Ofp: OFPPS_LINK_DOWN=1; OFPPC_PORT_DOWN=1; OFPPR_DELETE=1; OFPP_CONTROLLER=0xfffffffd; OFPCML_NO_BUFFER=0xffff; OFPIT_APPLY_ACTIONS=4; OFPTT_ALL=255; OFPFC_DELETE=3; OFPP_ANY=0xffffffff; OFPG_ANY=0xffffffff
class Parser:
    def OFPMatch(self, **k): return tuple(sorted(k.items()))
    def OFPActionOutput(self, p, *a): return ('out', p)
    def OFPActionSetQueue(self, q): return ('queue', q)
    def OFPInstructionActions(self, t, acts): return acts
    def OFPFlowMod(self, datapath=None, priority=0, match=None, instructions=None, command=None, **k): return ('mod', datapath.id, priority, match, tuple(instructions or ()), command)
    def OFPPortDescStatsRequest(self, dp, f): return ('portdesc', dp.id)
class DP:
    def __init__(s, i, table): s.id=i; s.ofproto=Ofp(); s.ofproto_parser=Parser(); s.table=table
    def send_msg(s, m):
        if m[0]=='mod' and m[5] is None: s.table[(m[2], m[3])] = m[4]
        elif m[0]=='mod': s.table.clear()
class Port:
    def __init__(s, no, state=0, config=0): s.port_no=no; s.state=state; s.config=config
tables = {1:{},2:{},3:{}}
c = ec.EmergencyController()
c.dps = {i: DP(i, tables[i]) for i in (1,2,3)}
for d in c.dps.values(): c.install_all(d)

def route(dpid, prio, ip, field=None):
    for (p, m), acts in tables[dpid].items():
        md = dict(m)
        if p==prio and md.get('ipv4_dst')==ip and (field is None or field in md): return [a for a in acts[0]] if isinstance(acts[0], list) else list(acts)
def show(title):
    print(title)
    print('   s1 emergency to h2 (s3 host):', route(1,100,'10.0.0.2','udp_dst'))
    print('   s1 normal    to h2          :', route(1,10,'10.0.0.2'))
    print('   s3 emergency to h1 (alert ACKs):', route(3,100,'10.0.0.1','udp_src'))
    print('   s3 normal    to h1          :', route(3,10,'10.0.0.1'))
    print('   s2 emergency to h2          :', route(2,100,'10.0.0.2','udp_dst'))
show('NORMAL (link up)')
c.set_port_state(c.dps[1], Port(3, state=1), False); c.set_port_state(c.dps[3], Port(5, state=1), False)
show('LINK DOWN (dynamic)')
c.set_port_state(c.dps[1], Port(3), False); c.set_port_state(c.dps[3], Port(5), False)
show('LINK UP again')
# static mode: no reaction
c.handle_event({'type':'mode','value':'static'})
c.set_port_state(c.dps[1], Port(3, state=1), False)
print('static after down: s1 normal to h2 =', route(1,10,'10.0.0.2'), ' direct_down =', c.direct_down)
c.handle_event({'type':'mode','value':'dynamic'})
show('SWITCH static->dynamic while link still down')
c.set_port_state(c.dps[1], Port(3), False)
show('link restored')
# unrelated port ignored
c.set_port_state(c.dps[1], Port(1, state=1), False); print('unrelated port ignored, direct_down =', c.direct_down)
