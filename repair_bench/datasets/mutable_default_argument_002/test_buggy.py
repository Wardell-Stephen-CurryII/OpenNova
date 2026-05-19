import pytest
from buggy import init_config, build_url


def test_init_config_default_size():
    cfg = init_config()
    # Should include debug flag but not grow across calls
    assert 'debug' in cfg
    assert len(cfg) == 3


def test_build_url_first():
    assert build_url('https', 'example.com') == 'https/example.com'


def test_build_url_does_not_accumulate():
    url = build_url('http', 'test.com')
    parts = url.split('/')
    assert len(parts) == 2
