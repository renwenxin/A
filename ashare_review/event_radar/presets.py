"""预置主题库（12 个）。节点名=东财概念板块名（实施时若网络不可用，先以手动代码池兜底）。"""
from .themes import Theme, ChainNode


def _n(node, concept=None, codes=None):
    return ChainNode(node=node, concept_name=concept or node, manual_codes=codes or [])


PRESET_THEMES = [
    Theme(id='ai_compute', name='AI算力', chain_nodes=[
        _n('液冷服务器'), _n('光模块'), _n('PCB'), _n('铜缆高速连接'), _n('电源设备'), _n('MLCC')]),
    Theme(id='low_altitude', name='低空经济', chain_nodes=[
        _n('低空经济'), _n('eVTOL'), _n('无人机'), _n('碳纤维')]),
    Theme(id='humanoid_robot', name='人形机器人', chain_nodes=[
        _n('减速器'), _n('伺服电机'), _n('丝杠'), _n('传感器'), _n('机器人概念')]),
    Theme(id='solid_battery', name='固态电池', chain_nodes=[
        _n('固态电池'), _n('锂电池'), _n('锂电设备')]),
    Theme(id='innovative_drug', name='创新药', chain_nodes=[
        _n('创新药'), _n('CRO'), _n('减肥药'), _n('ADC')]),
    Theme(id='satellite_net', name='卫星互联网', chain_nodes=[
        _n('卫星互联网'), _n('卫星导航'), _n('北斗导航')]),
    Theme(id='commercial_space', name='商业航天', chain_nodes=[
        _n('商业航天'), _n('航空发动机'), _n('军工电子')]),
    Theme(id='semiconductor', name='半导体', chain_nodes=[
        _n('半导体设备'), _n('半导体材料'), _n('先进封装'), _n('存储芯片')]),
    Theme(id='data_element', name='数据要素', chain_nodes=[
        _n('数据要素'), _n('数据确权'), _n('国资云')]),
    Theme(id='military', name='军工', chain_nodes=[
        _n('军工'), _n('航空发动机'), _n('国防军工')]),
    Theme(id='power_grid', name='电力设备', chain_nodes=[
        _n('特高压'), _n('电网设备'), _n('充电桩'), _n('虚拟电厂')]),
    Theme(id='ai_glasses', name='AI眼镜', chain_nodes=[
        _n('AI眼镜'), _n('消费电子'), _n('光学元件')]),
]


def seed_default_themes(store) -> int:
    """themes.json 为空时写入预置主题，返回新增数量。"""
    if store.load():
        return 0
    for t in PRESET_THEMES:
        store.add(t)
    return len(PRESET_THEMES)
