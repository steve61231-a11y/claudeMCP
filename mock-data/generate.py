"""Generates fixture JSON files simulating ingested posts/comments per platform.

Includes direct mentions, indirect/implied mentions (no name, just title/region),
and comment threads, to exercise indirect-mention matching, sentiment spread,
and narrative clustering in the engine.
"""
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent / "fixtures"
OUT_DIR.mkdir(exist_ok=True)

POLITICIAN_NAME = "Wanjiru Kamau"
POLITICIAN_TITLE = "Governor"
POLITICIAN_REGION = "Nakuru"

NOW = datetime(2026, 6, 22, 12, 0, 0)


def ts(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


DIRECT_TEMPLATES = [
    ("positive", "{name} just announced a new hospital for {region}, this is huge progress!"),
    ("positive", "Big respect to {name} for fixing the {region} water crisis. Real leadership."),
    ("negative", "{name} promised roads in {region} two years ago. Still nothing. Disappointed."),
    ("negative", "Why is {name} silent on the {region} unemployment numbers? Unacceptable."),
    ("neutral", "{name} is scheduled to speak at the {region} youth forum next week."),
]

INDIRECT_TEMPLATES = [
    ("positive", "The {title} from {region} really delivered on the new market stalls. Great work."),
    ("negative", "Our {title} keeps making promises about {region} roads with nothing to show."),
    ("neutral", "Heard the {title} of {region} is attending the regional summit tomorrow."),
    ("negative", "The county boss running {region} needs to explain the missing relief funds."),
]

NARRATIVE_SEEDS = [
    ("infrastructure", [
        "Roads in {region} are still in terrible shape, when will this be fixed?",
        "New tarmac road in {region} finally complete after years of delay.",
        "The {title} of {region} cut the ribbon on the new bridge today.",
    ]),
    ("healthcare", [
        "{name} opened a new maternity wing at {region} county hospital.",
        "Shortage of medicine at {region} clinics continues despite promises.",
        "Health workers in {region} praise the new equipment from the county.",
    ]),
    ("corruption", [
        "Allegations of missing funds in the {region} county budget need investigation.",
        "{name} denies any wrongdoing in the {region} tender scandal.",
        "Auditors flag irregularities in {region} county spending report.",
    ]),
]

PLATFORMS = ["x", "facebook", "linkedin", "tiktok"]
AUTHORS = [f"user_{i:03d}" for i in range(1, 40)]


def make_engagement(platform: str) -> dict:
    base = {"x": (5, 500), "facebook": (2, 300), "linkedin": (1, 150), "tiktok": (10, 2000)}[platform]
    likes = random.randint(*base)
    return {
        "likes": likes,
        "shares": random.randint(0, likes // 4 + 1),
        "comments": random.randint(0, likes // 6 + 1),
    }


def build_posts():
    posts_by_platform = {p: [] for p in PLATFORMS}

    def add(text: str, sentiment_hint: str, source_type: str = "post"):
        platform = random.choice(PLATFORMS)
        posts_by_platform[platform].append(
            {
                "author_handle": random.choice(AUTHORS),
                "text": text,
                "posted_at": ts(random.uniform(0, 13)),
                "source_type": source_type,
                "engagement": make_engagement(platform),
                "sentiment_hint": sentiment_hint,
            }
        )

    for sentiment, template in DIRECT_TEMPLATES:
        for _ in range(3):
            add(template.format(name=POLITICIAN_NAME, region=POLITICIAN_REGION), sentiment)

    for sentiment, template in INDIRECT_TEMPLATES:
        for _ in range(3):
            add(template.format(title=POLITICIAN_TITLE, region=POLITICIAN_REGION), sentiment)

    for label, templates in NARRATIVE_SEEDS:
        for template in templates:
            for _ in range(4):
                add(template.format(name=POLITICIAN_NAME, region=POLITICIAN_REGION, title=POLITICIAN_TITLE), "neutral")

    # comment threads replying to posts
    comment_texts = [
        "Totally agree with this.",
        "Not buying it, show us proof.",
        "This is exactly what {region} needed.",
        "Same old promises from {name}.",
    ]
    for text in comment_texts:
        for _ in range(5):
            add(text.format(region=POLITICIAN_REGION, name=POLITICIAN_NAME), "neutral", source_type="comment")

    return posts_by_platform


def main():
    posts_by_platform = build_posts()
    for platform, posts in posts_by_platform.items():
        path = OUT_DIR / f"{platform}.json"
        path.write_text(json.dumps(posts, indent=2))
        print(f"wrote {len(posts)} mentions to {path}")


if __name__ == "__main__":
    main()
