from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_app_renders_main_sections():
    app_file = Path(__file__).resolve().parents[1] / "app.py"
    at = AppTest.from_file(app_file)
    at.run()

    assert at.title[0].value == "Event-Triggered Bitcoin Automation Agent"
    joined = "\n".join(m.value for m in at.markdown)
    assert "Paper Book" in joined
