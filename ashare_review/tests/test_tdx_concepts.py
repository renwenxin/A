"""TDX 本地概念库解析测试"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _sample():
    # 模拟 infoharbor_block.dat 片段
    return ('#GN_一带一路,3,880594,20130912,20260814,,' + chr(13) + chr(10)
            + '0#000008,1#600009,2#920445,' + chr(13) + chr(10)
            + '#GN_白酒概念,2,880567,20120831,20251110,,' + chr(13) + chr(10)
            + '0#000858,1#600519,' + chr(13) + chr(10))


def test_parse_infoharbor_block(tmp_path):
    from ashare_review.data.tdx_concepts import parse_infoharbor_block
    p = tmp_path / 'block.dat'
    p.write_text(_sample(), encoding='gbk')
    concepts = parse_infoharbor_block(str(p))
    assert set(concepts.keys()) == {'一带一路', '白酒概念'}
    assert concepts['一带一路'] == ['000008', '600009', '920445']
    assert concepts['白酒概念'] == ['000858', '600519']


def test_parse_market_mapping(tmp_path):
    from ashare_review.data.tdx_concepts import parse_infoharbor_block, market_of
    assert market_of(0) == 'sz' and market_of(1) == 'sh' and market_of(2) == 'bj'
    p = tmp_path / 'block.dat'
    p.write_text('#GN_测试,2,,,,' + chr(13) + chr(10) + '0#000001,1#600001,' + chr(13) + chr(10), encoding='gbk')
    concepts = parse_infoharbor_block(str(p))
    assert concepts['测试'] == ['000001', '600001']


def test_build_concept_map(tmp_path):
    from ashare_review.data.tdx_concepts import build_concept_map
    p = tmp_path / 'block.dat'
    p.write_text('#GN_测试,2,,,,' + chr(13) + chr(10) + '0#000001,1#600001,' + chr(13) + chr(10), encoding='gbk')
    cm = build_concept_map(str(p))
    assert 'concepts' in cm and '测试' in cm['concepts']
    c = cm['concepts']['测试']
    assert c['members']['000001'] == 1 and c['members']['600001'] == 1
    assert c['source'] == 'tdx' and c['partial'] is False
