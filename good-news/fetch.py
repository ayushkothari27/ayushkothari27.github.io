"""Fetch news from RSS feeds, keep only the uplifting stories, write feed.json.

Run by .github/workflows/good-news.yml on a schedule. Locally:
    pip install -r good-news/requirements.txt && python good-news/fetch.py
"""

import html
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

OUT = Path(__file__).parent / "feed.json"
MAX_ITEMS = 120
MAX_AGE_DAYS = 14
UA = "Mozilla/5.0 (compatible; good-news-feed/1.0)"

# (name, url, threshold). Sources that are already positive by design get a
# low bar; general news has to clear a high one.
FEEDS = [
    ("Good News Network", "https://www.goodnewsnetwork.org/feed/", 0.05),
    ("Reasons to be Cheerful", "https://reasonstobecheerful.world/feed/", 0.05),
    ("Optimist Daily", "https://www.optimistdaily.com/feed/", 0.05),
    ("r/UpliftingNews", "https://www.reddit.com/r/UpliftingNews/top/.rss?t=day", 0.05),
    ("Science Daily", "https://www.sciencedaily.com/rss/top/science.xml", 0.5),
    ("NPR", "https://feeds.npr.org/1001/rss.xml", 0.6),
    ("BBC", "https://feeds.bbci.co.uk/news/rss.xml", 0.6),
]

# Hard no, whatever the sentiment score says ("killed it!" reads as positive).
BLOCK = re.compile(
    r"\b(kill(s|ed|ing)?|murder\w*|dead|death|dies|died|war|shoot\w*|shot|stab\w*|"
    r"attack\w*|terror\w*|bomb\w*|rape\w*|abuse\w*|assault\w*|massacre|"
    r"crash(es|ed)?|tragic|tragedy|suicide|hostage|genocide|trump|election|midterms?)\b",
    re.I,
)

# Recurring filler posts and sections that aren't news.
SKIP_TITLE = re.compile(r"^(good news in history|podcast transcript|what we.re reading)|horoscope", re.I)
SKIP_LINK = re.compile(r"/(sport|sounds|iplayer|videos?)/", re.I)

TAGS = re.compile(r"<[^>]+>")
analyzer = SentimentIntensityAnalyzer()


def clean(text, limit=None):
    text = html.unescape(TAGS.sub(" ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    # Reddit summaries are just "submitted by /u/... [link] [comments]".
    text = re.sub(r"submitted by /u/\S+.*$", "", text).strip()
    if limit and len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


def image_for(entry):
    for key in ("media_content", "media_thumbnail"):
        for m in entry.get(key) or []:
            if m.get("url"):
                return m["url"]
    for link in entry.get("links") or []:
        if link.get("type", "").startswith("image/"):
            return link.get("href")
    m = re.search(r'<img[^>]+src="([^"]+)"', entry.get("summary", "") or "")
    return m.group(1) if m else None


def published(entry):
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if t:
        return datetime.fromtimestamp(time.mktime(t), timezone.utc)
    return datetime.now(timezone.utc)


def fetch(name, url, threshold):
    parsed = feedparser.parse(url, agent=UA)
    if parsed.bozo and not parsed.entries:
        print(f"  ! {name}: {parsed.get('bozo_exception')}", file=sys.stderr)
        return []
    kept = []
    for e in parsed.entries:
        title = clean(e.get("title"))
        link = e.get("link")
        if not title or not link or SKIP_TITLE.search(title) or SKIP_LINK.search(link):
            continue
        summary = clean(e.get("summary"), 240)
        text = f"{title}. {summary}"
        if BLOCK.search(text):
            continue
        score = analyzer.polarity_scores(text)["compound"]
        if score < threshold:
            continue
        kept.append({
            "title": title,
            "link": link,
            "source": name,
            "summary": summary,
            "image": image_for(e),
            "published": published(e).isoformat(timespec="seconds"),
            "score": round(score, 3),
        })
    print(f"  {name}: kept {len(kept)}/{len(parsed.entries)}")
    return kept


def main():
    old = []
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text())["items"]
        except (ValueError, KeyError):
            pass

    fresh = []
    for feed in FEEDS:
        try:
            fresh += fetch(*feed)
        except Exception as exc:  # one bad feed shouldn't sink the run
            print(f"  ! {feed[0]}: {exc}", file=sys.stderr)

    # New items win over old copies of the same link; drop stale ones.
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    by_link = {}
    for item in old + fresh:
        by_link[item["link"]] = item
    seen_titles = set()
    items = []
    for item in sorted(by_link.values(), key=lambda i: i["published"], reverse=True):
        key = item["title"].lower()
        if key in seen_titles or datetime.fromisoformat(item["published"]) < cutoff:
            continue
        seen_titles.add(key)
        items.append(item)
    items = items[:MAX_ITEMS]

    if not fresh and old:
        print("No feeds responded; keeping the existing feed.json", file=sys.stderr)
        return

    old_items = old[:MAX_ITEMS]
    if items == old_items:
        print("No changes.")
        return
    OUT.write_text(json.dumps({
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "items": items,
    }, indent=1, ensure_ascii=False) + "\n")
    print(f"Wrote {len(items)} items to {OUT}")


if __name__ == "__main__":
    main()
