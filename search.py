import urllib.request, urllib.parse, json

def web_search(query: str, max_results=3) -> list[dict]:
    try:
        params = urllib.parse.urlencode({
            "q": query, "format": "json", "no_html": "1", "skip_disambig": "1"
        })
        req = urllib.request.Request(
            f"https://api.duckduckgo.com/?{params}",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=8) as res:
            data = json.loads(res.read().decode())

        results = []
        if data.get("AbstractText"):
            results.append({
                "title": data.get("Heading", ""),
                "snippet": data["AbstractText"][:400],
                "url": data.get("AbstractURL", ""),
            })
        for t in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(t, dict) and t.get("Text"):
                results.append({
                    "title": t.get("FirstURL","").split("/")[-1].replace("_"," "),
                    "snippet": t["Text"][:300],
                    "url": t.get("FirstURL",""),
                })
            if len(results) >= max_results:
                break
        return results
    except Exception as e:
        print(f"[search] {e}")
        return []

def format_results(results: list[dict]) -> str:
    return "\n".join(f"- {r['title']}: {r['snippet']}" for r in results) if results else ""
