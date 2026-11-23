from app_research_agent.evaluate import field_correct

HUB = {"core": ["OAUTH2", "BEARER_TOKEN"], "optional": ["API_KEY", "JWT"]}


def test_set_fields_core_and_optional():
    assert field_correct("auth_methods", ["OAUTH2", "BEARER_TOKEN"], HUB)
    assert field_correct("auth_methods", ["OAUTH2", "API_KEY", "JWT"], HUB)  # API_KEY ~ BEARER
    assert not field_correct("auth_methods", ["OAUTH2"], HUB)  # missing a core method
    assert not field_correct(
        "auth_methods", ["OAUTH2", "BEARER_TOKEN", "BASIC"], HUB
    )  # wrong extra


def test_enum_with_accept():
    assert field_correct("access", "SELF_SERVE_TRIAL", "SELF_SERVE_FREE", ["SELF_SERVE_TRIAL"])
    assert not field_correct("access", "PAID_PLAN", "SELF_SERVE_FREE", ["SELF_SERVE_TRIAL"])
    assert field_correct("official_mcp", False, False)
