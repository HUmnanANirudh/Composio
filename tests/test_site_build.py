import json
import re

from app_research_agent.site_build import PAGES, standalone

HTML = """<a href="llms.txt">l</a><a href="data.json">d</a><a href="#run">Run it</a>
<script>
fetch("data.json").then(r => r.json()).then(render)
</script>"""


def test_standalone_embeds_data_and_links_live_site():
    out = standalone(HTML, {"note": "</script> inside data"})
    assert 'fetch("data.json")' not in out
    assert f'href="{PAGES}llms.txt"' in out and f'href="{PAGES}data.json"' in out
    assert f'href="{PAGES}">Live site' in out
    embedded = re.search(
        r'<script id="bundle" type="application/json">(.*?)</script>', out, re.DOTALL
    )
    assert json.loads(embedded.group(1)) == {"note": "</script> inside data"}
