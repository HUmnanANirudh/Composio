from app_research_agent.apps import load_apps, select


def test_loads_100_unique_apps():
    apps = load_apps()
    assert len(apps) == 100
    assert len({a.id for a in apps}) == 100
    assert len({a.category for a in apps}) == 10


def test_hint_url_parsing():
    by = {a.app: a for a in load_apps()}
    assert by["Zoho CRM"].hint_url == "https://zoho.com/crm"
    assert by["Harvest"].hint_url == "https://harvestapp.com"
    assert by["Paygent Connect"].hint_url is None  # hint has no domain


def test_select():
    apps = load_apps()
    assert [a.id for a in select(apps, "1-3,99")] == [1, 2, 3, 99]
    assert [a.app for a in select(apps, "slack,Stripe")] == ["Slack", "Stripe"]
