from aiops_challenge_2026.config import load_public_config


def test_public_network_elements():
    config = load_public_config("network_elements")
    assert config["public_id_format"] == "<city>-<original-role>"
    assert config["cities"] == ["beida", "shenyang", "xian", "chengdu", "wuhan", "shanghai", "nanjing", "guangzhou"]
    assert "service-vm-3" in config["device_roles"]


def test_public_taxonomy_has_no_mapping_rules():
    config = load_public_config("fault_taxonomy")
    assert set(config) == {"major_categories", "sub_categories"}
    assert all(item.split("_", 1)[0] in config["major_categories"] for item in config["sub_categories"])
