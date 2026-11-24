import json

import httpx

from shopflow.ingest.translate import LibreTranslate


def test_libretranslate_sends_batch_and_parses_list():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"translatedText": ["I recommend", "Very good"]})

    lt = LibreTranslate(
        "http://lt:5000/", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    out = lt.translate(["Recomendo", "Muito bom"], "pt", "en")

    assert out == ["I recommend", "Very good"]
    assert seen["url"] == "http://lt:5000/translate"
    assert seen["body"] == {
        "q": ["Recomendo", "Muito bom"],
        "source": "pt",
        "target": "en",
        "format": "text",
    }
