// Demo dataset — backed by the real pipeline run for Edwin Sifuna
// (engine.pipeline.run_pipeline, live SocialCrawl ingestion, 55 linked mentions).
// Until the API is wired up to trigger live runs per-politician, every search
// resolves to this dataset so the front end can be reviewed end-to-end.

const REPORTS = {
  "edwin sifuna": {
    name: "Edwin Sifuna",
    title: "Nairobi Senator / ODM Secretary-General (contested)",
    window: "Dec 1, 2025 – Jun 29, 2026",
    generated: "2026-06-29",
    summary:
      "A major ODM-loyalty escalation dominates the final 72 hours of the window: 17 of 55 mentions landed in the last 3 days alone, driven almost entirely by YouTube political-commentary channels rather than legacy press. Sentiment is decisively negative overall, with a small but high-engagement positive pocket centered on a birthday tribute and a reconciliation pledge to Ida Odinga.",

    sentiment: { negative: 58.3, neutral: 35.4, positive: 6.2, totalAnalyzed: 48 },

    volume: {
      total: 55,
      platforms: 17,
      last72h: 17,
      byPlatform: [
        { platform: "YouTube", count: 18, share: 32.7 },
        { platform: "LinkedIn", count: 10, share: 18.2 },
        { platform: "newshub.co.ke", count: 5, share: 9.1 },
        { platform: "citynews.co.ke", count: 3, share: 5.5 },
        { platform: "nation.africa", count: 3, share: 5.5 },
        { platform: "kahawatungu.com", count: 3, share: 5.5 },
        { platform: "nairobiwire.com", count: 2, share: 3.6 },
        { platform: "tv47.digital", count: 2, share: 3.6 },
        { platform: "other (9 outlets)", count: 9, share: 16.4 },
      ],
      byDay: [
        { date: "2026-06-29", count: 12 },
        { date: "2026-06-28", count: 4 },
        { date: "2026-06-27", count: 1 },
        { date: "2026-06-25", count: 1 },
        { date: "2026-06-23", count: 2 },
        { date: "earlier (back to 2019)", count: 35 },
      ],
      engagement: {
        youtubeViews: 494000,
        linkedinLikes: 564,
        linkedinComments: 38,
        linkedinShares: 6,
        topPost: { label: "Citizen TV — Sifuna vs. MP Kaguchia exchange", value: "323,763 views", url: "https://www.youtube.com/watch?v=Eq7lE1qGs2o" },
        topLinkedin: { label: "Senate birthday surprise (The Statesman Digital)", value: "391 likes / 11 comments", url: "https://www.linkedin.com/feed/update/urn:li:activity:7463530362556751872" },
      },
      topMentions: [
        { platform: "YouTube", metric: "323,763 views", sentiment: "negative", headline: "Senator Sifuna to MP John Kaguchia: I am not the one belittling you...", url: "https://www.youtube.com/watch?v=Eq7lE1qGs2o" },
        { platform: "YouTube", metric: "46,053 views", sentiment: "negative", headline: "Edwin Sifuna disinherited from ODM family creating rich fodder...", url: "https://www.youtube.com/watch?v=hDIROM3tIlM" },
        { platform: "YouTube", metric: "21,374 views", sentiment: "negative", headline: "Finished! Edwin Sifuna Wins Big and Vindicated as Wanga Burst In Anger", url: "https://www.youtube.com/watch?v=OXI76yoIJPs" },
        { platform: "LinkedIn", metric: "391 likes", sentiment: "positive", headline: "Watch Edwin Sifuna's reaction as Senators and friends surprise him on his birthday!", url: "https://www.linkedin.com/feed/update/urn:li:activity:7463530362556751872" },
        { platform: "LinkedIn", metric: "64 likes / 13 comments", sentiment: "positive", headline: "This is the most hilarious interview yet. Senator Edwin Sifuna said 2 things...", url: "https://www.linkedin.com/feed/update/urn:li:activity:7420306900149391360" },
        { platform: "newshub.co.ke", metric: "article", sentiment: "negative", headline: "Angry Kasmuel McOure Lectures Edwin Sifuna Over 'Mediocre' Remark About Oburu Odinga", url: "https://newshub.co.ke/category/breaking-news/page/19/" },
        { platform: "citynews.co.ke", metric: "article", sentiment: "negative", headline: "Exposed: key ODM official reveals State House night meeting that led to Sifuna's removal as SG", url: "https://citynews.co.ke/exposed-key-odm-official-reveals-state-house-night-meeting-that-led-to-sifunas-removal-as-sg-ruto-is-ruthless/" },
      ],
    },

    influence: [
      { rank: 1, score: 4.4, who: "TV47 Digital", platform: "News outlet", note: "2 articles (IMF slave / weak judiciary remarks, 2024)", sentiment: -8.0 },
      { rank: 1, score: 4.4, who: "afrodavidtv", platform: "YouTube", note: "2 videos on the Meru/Wanga drama", sentiment: -8.0 },
      { rank: 1, score: 4.4, who: "KENYAPOLITICSTV-m2m", platform: "YouTube", note: "\"Wanga Burst In Anger\" + \"Ruto Begging Sifuna\"", sentiment: -8.0 },
      { rank: 1, score: 4.4, who: "kenyalens", platform: "YouTube", note: "Ally-defection / Wetangula angle", sentiment: -8.0 },
      { rank: 5, score: 4.2, who: "Editor (newshub.co.ke)", platform: "News outlet", note: "3 articles across 2025", sentiment: -4.0 },
      { rank: 6, score: 3.5, who: "The Statesman Digital", platform: "LinkedIn", note: "Most-liked content in the dataset (birthday + reconciliation post)", sentiment: 5.0 },
      { rank: 7, score: 3.2, who: "News Hub", platform: "News outlet", note: "2 articles", sentiment: -4.0 },
      { rank: 7, score: 3.2, who: "Rogers_Lugose", platform: "YouTube", note: "\"Caleb Amisi Turns On Sifuna\"", sentiment: -4.0 },
      { rank: 9, score: 2.9, who: "Abel Sawe (citynews.co.ke)", platform: "News outlet", note: "State House night-meeting exposé", sentiment: -3.0 },
      { rank: 10, score: 2.2, who: "cfeditoren (africa-press.net)", platform: "News outlet", note: "1 article (2020 BBI-era)", sentiment: -4.0 },
    ],

    network: {
      nodes: [
        { id: "sifuna", label: "Edwin Sifuna", group: "core", value: 30 },
        { id: "orengo", label: "James Orengo", group: "ally", value: 6 },
        { id: "owino", label: "Babu Owino", group: "ally", value: 7 },
        { id: "osotsi", label: "Godfrey Osotsi", group: "ally", value: 7 },
        { id: "amisi", label: "Caleb Amisi", group: "unstable", value: 5 },
        { id: "natembeya", label: "Natembeya (unconfirmed)", group: "unconfirmed", value: 3 },
        { id: "wanga", label: "Gladys Wanga", group: "rival", value: 9 },
        { id: "oburu", label: "Oburu Oginga", group: "institutional", value: 5 },
        { id: "ida", label: "Ida Odinga", group: "swing", value: 5 },
        { id: "ruto", label: "William Ruto", group: "rival", value: 8 },
        { id: "wetangula", label: "Moses Wetangula", group: "rival", value: 7 },
        { id: "mcoure", label: "Kasmuel McOure", group: "critic", value: 4 },
        { id: "asige", label: "Crystal Asige", group: "centrist", value: 3 },
      ],
      edges: [
        { from: "sifuna", to: "orengo", label: "coalition" },
        { from: "sifuna", to: "owino", label: "coalition" },
        { from: "sifuna", to: "osotsi", label: "coalition" },
        { from: "sifuna", to: "amisi", label: "defection claim", dashed: true },
        { from: "sifuna", to: "natembeya", label: "unconfirmed alliance", dashed: true },
        { from: "sifuna", to: "wanga", label: "ouster conflict" },
        { from: "wanga", to: "oburu", label: "succession" },
        { from: "oburu", to: "ida", label: "party legacy" },
        { from: "sifuna", to: "ida", label: "reconciliation pledge" },
        { from: "sifuna", to: "ruto", label: "strategic interest" },
        { from: "sifuna", to: "wetangula", label: "base-erosion claim", dashed: true },
        { from: "wetangula", to: "ruto", label: "Kenya Kwanza ally" },
        { from: "sifuna", to: "mcoure", label: "public clash" },
        { from: "sifuna", to: "asige", label: "measured concern" },
        { from: "osotsi", to: "wanga", label: "went public against" },
      ],
      legend: [
        { group: "core", label: "Subject", color: "#7dd3fc" },
        { group: "ally", label: "Coalition / ally", color: "#86efac" },
        { group: "unstable", label: "Possible defection", color: "#fbbf24" },
        { group: "unconfirmed", label: "Unconfirmed claim", color: "#94a3b8" },
        { group: "rival", label: "Rival / adversary", color: "#fb7185" },
        { group: "institutional", label: "Institutional", color: "#c4b5fd" },
        { group: "swing", label: "Swing influence", color: "#f0abfc" },
        { group: "critic", label: "Critic", color: "#fdba74" },
        { group: "centrist", label: "Centrist", color: "#a5f3fc" },
      ],
    },

    narratives: [
      { label: "Sifuna's ODM Loyalty Crisis", strength: 23.0, growth: 0.09, mentions: 23,
        description: "Edwin Sifuna faces mounting internal pressure and accusations of factionalism within ODM, drawing a firm line against any alliance with UDA while positioning himself as a principled opposition voice ahead of 2027." },
      { label: "Sifuna Political Turbulence", strength: 8.0, growth: 0.0, mentions: 8,
        description: "Social media posts swirl with dramatic and often contradictory claims about Edwin Sifuna's political standing, including rumors of his ODM resignation, shifting alliances, and his perceived impact on rivals like Wetangula and Ruto." },
    ],

    risks: [
      "Single-source claims (\"new party with Natembeya\", \"Wetangula's base defecting\") are currently carried by commentary-channel YouTube only — if they don't hold up, Sifuna risks being tied to a wave of inaccurate coverage regardless of his own conduct.",
      "If the Caleb Amisi defection is real, it's the first concrete sign the Linda Mwananchi coalition isn't unified — a bigger risk than external ODM pressure.",
      "Pattern fatigue: this is at least the third distinct \"Sifuna vs. ODM leadership\" flashpoint (Feb removal exposé → April McOure clash → June exit/realignment) — recurring conflict risks the framing shifting from \"principled holdout\" to \"permanent party chaos.\"",
      "The McOure exchange is a sharper, more personal reputational exposure than institutional friction — a named-individual public confrontation, easily quotable against him.",
    ],

    opportunities: [
      "The Mama Ida Odinga reconciliation pledge remains Sifuna's strongest positive asset in the dataset — specific, quotable, and directly undercuts any \"destroying the party\" framing.",
      "The Statesman Digital's friendly coverage (391 likes, the highest engagement in the whole dataset) shows a real, already-engaged friendly outlet worth cultivating further.",
      "If the Wetangula-base-defection claims are accurate, this is a major opportunity to claim cross-party legitimacy beyond ODM/Luo-community politics — but only once confirmed by a non-commentary-channel source.",
      "Crystal Asige's measured-concern framing is an opening for direct engagement with an elected ally who hasn't fully turned hostile.",
    ],

    trends: [
      "Whether mainstream outlets (Nation, Standard, Citizen Digital, Star) corroborate the Natembeya new-party claim and the Wetangula-defection claim in the next 48–72 hours.",
      "Whether the Amisi situation is an isolated YouTube claim or a real Linda Mwananchi fracture.",
      "Gladys Wanga's posture swinging between \"burst in anger\" and \"begs Sifuna to come back\" within the same news cycle — worth tracking as a signal of how settled ODM's institutional position actually is.",
    ],
  },
};

function findReport(query) {
  const key = query.trim().toLowerCase();
  return REPORTS[key] || null;
}
