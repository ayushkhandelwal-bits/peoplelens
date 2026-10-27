"""Template library for fictional employee survey comments.

Comments are assembled from varied templates so that wording, length and tone
differ. The library deliberately includes neutral remarks, mixed comments that
touch two themes with opposite sentiment, hedged statements, and sarcasm that a
lexicon-based sentiment model is likely to misread. The *intended* theme and
sentiment are written to a separate evaluation file (never loaded into the
analytical database) so the NLP module can be evaluated honestly.
"""

from __future__ import annotations

import numpy as np

THEMES = ["Career growth", "Manager support", "Workload", "Compensation", "Recognition",
          "Work-life balance", "Learning & development", "Team collaboration"]

POSITIVE = {
    "Career growth": [
        "I can see a clear path to the next level here and my lead talks about it openly.",
        "Got promoted this cycle and the process felt fair and transparent.",
        "There are real opportunities to take on bigger responsibilities if you ask.",
        "Internal moves are encouraged, which has helped me grow quickly.",
    ],
    "Manager support": [
        "My manager is supportive and always makes time for one-on-ones.",
        "Really appreciate how my lead backs the team in difficult conversations.",
        "Good manager, gives honest feedback and trusts us to deliver.",
        "My reporting manager genuinely cares about our wellbeing.",
    ],
    "Workload": [
        "Workload is manageable and priorities are clear most weeks.",
        "The team is well staffed now, so the pressure has eased a lot.",
        "Deadlines are realistic and planning has improved this quarter.",
    ],
    "Compensation": [
        "Pay is competitive for my role and the increment was fair.",
        "Happy with the revised salary and the benefits are good.",
        "The bonus payout this year was better than I expected.",
    ],
    "Recognition": [
        "Good work gets noticed here, the shout-outs in team meetings mean a lot.",
        "I feel valued and recognised for what I contribute.",
        "Leadership acknowledged our launch publicly, which was motivating.",
    ],
    "Work-life balance": [
        "Flexible hours make it easy to balance work and family.",
        "I rarely have to work weekends and leave requests are approved quickly.",
        "Hybrid working has been great for my work-life balance.",
    ],
    "Learning & development": [
        "The training programmes have been genuinely useful for my day-to-day work.",
        "Loved the leadership course, I am already applying what I learnt.",
        "Plenty of learning budget and time set aside to use it.",
    ],
    "Team collaboration": [
        "Great team, people help each other without being asked.",
        "Collaboration across teams has improved a lot this year.",
        "My colleagues are the best part of working here.",
    ],
}

NEGATIVE = {
    "Career growth": [
        "No clear growth path, I have been in the same role for years.",
        "Promotions seem to depend on visibility rather than performance.",
        "Hard to see where my career goes from here, nobody discusses it.",
        "Internal openings are filled from outside before we even hear about them.",
    ],
    "Manager support": [
        "My manager is rarely available and feedback only comes at appraisal time.",
        "Changed managers twice this year and nobody explained why, very unsettling.",
        "Micromanagement is exhausting, there is no trust in the team.",
        "I do not feel my manager supports me when issues come up.",
    ],
    "Workload": [
        "Workload is too high, we are constantly firefighting.",
        "Too many open positions so the rest of us are covering extra work.",
        "Targets keep increasing every month without any extra support.",
        "Burnout is real on our team, we are stretched thin.",
    ],
    "Compensation": [
        "Salary is below market, new joiners are paid more than people with experience.",
        "The increment did not even match inflation.",
        "Pay is the main reason people on my team are leaving.",
        "Compensation is not competitive and the bonus structure is unclear.",
    ],
    "Recognition": [
        "Nobody notices the extra effort we put in.",
        "Recognition goes to the same few people every time.",
        "I feel invisible, good work is simply expected and never acknowledged.",
    ],
    "Work-life balance": [
        "Late-night calls and weekend work have become normal, it is not sustainable.",
        "Hard to switch off, messages keep coming after hours.",
        "Shift timings change at short notice which makes planning impossible.",
    ],
    "Learning & development": [
        "No time to attend training even when it is approved.",
        "The courses on offer are generic and not relevant to my work.",
        "Learning budget exists on paper but approvals never come through.",
    ],
    "Team collaboration": [
        "Teams work in silos and handoffs between departments are painful.",
        "There is a lot of blame when things go wrong instead of solving problems.",
        "Communication across teams is poor and decisions are not shared.",
    ],
}

NEUTRAL = [
    "No major concerns at the moment.",
    "Things are okay, nothing specific to add this time.",
    "It has been an average quarter.",
    "Would be good to see the survey results shared with teams.",
    "The new office layout is fine, still getting used to it.",
    "Not much has changed since the last survey.",
    "Please keep the survey shorter next time.",
    "Some processes changed this quarter, waiting to see how it settles.",
]

NEUTRAL_THEMED = {
    "Workload": "Workload has been about the same as last quarter.",
    "Compensation": "Would like more clarity on how the salary bands are decided.",
    "Career growth": "It would help to understand the criteria for the next level.",
    "Learning & development": "Is there a list of training programmes available for my level?",
    "Manager support": "We moved to a new manager recently, too early to say how it is going.",
    "Recognition": "Maybe the recognition programme could be explained better.",
    "Work-life balance": "Hybrid policy seems to differ between teams.",
    "Team collaboration": "We had a team restructure, still figuring out who owns what.",
}

MIXED = [
    ("Manager support", "Compensation", "My manager is great, but the pay is well below market."),
    ("Team collaboration", "Workload", "Love my team, though the workload has been brutal lately."),
    ("Learning & development", "Career growth",
     "The training is good, but there is no promotion at the end of it."),
    ("Work-life balance", "Compensation", "Flexible hours are a big plus, salary not so much."),
    ("Recognition", "Workload", "We get recognised for our work, but there is simply too much of it."),
    ("Career growth", "Manager support", "Plenty of opportunities to grow, but my manager is rarely around."),
]

SARCASTIC = [
    ("Workload", "Great, another quarter of doing two people's jobs. Love it."),
    ("Manager support", "Fantastic, a third new manager this year. Really building stability."),
    ("Compensation", "Wonderful increment, it almost covers my commute."),
    ("Recognition", "Sure, the pizza party totally makes up for the weekend work."),
    ("Career growth", "Amazing career growth, if you count new acronyms as growth."),
]

HEDGED_NEG = [
    ("Workload", "Not sure the workload is sustainable if two more people leave."),
    ("Career growth", "I don't hate it here, but I'm not sure there's a future for me."),
    ("Compensation", "Pay is not terrible, just not great either."),
]


def _style(text: str, rng: np.random.Generator) -> str:
    u = rng.random()
    if u < 0.12:
        text = text.lower()
    elif u < 0.2:
        text = text.rstrip(".")
    if rng.random() < 0.07:
        text = text + " Thanks."
    return text


def make_comment(rng: np.random.Generator, scores: dict, comp_low: bool) -> tuple[str, str, str]:
    """Return (text, intended_theme, intended_sentiment).

    ``scores`` holds the respondent's engagement sub-scores (1-5).
    """
    dims = {
        "Manager support": scores["manager_support"],
        "Work-life balance": scores["work_life_balance"],
        "Career growth": scores["career_growth"],
        "Recognition": scores["job_satisfaction"],
    }
    if comp_low:
        dims["Compensation"] = min(scores["career_growth"], 2.8)
    u = rng.random()
    if u < 0.10:
        return _style(NEUTRAL[rng.integers(len(NEUTRAL))], rng), "General", "neutral"
    if u < 0.17:
        t1, t2, text = MIXED[rng.integers(len(MIXED))]
        return _style(text, rng), t1 + " | " + t2, "mixed"
    if u < 0.22 and scores["engagement"] < 3.4:
        th, text = SARCASTIC[rng.integers(len(SARCASTIC))]
        return text, th, "negative"
    if u < 0.26 and scores["engagement"] < 3.6:
        th, text = HEDGED_NEG[rng.integers(len(HEDGED_NEG))]
        return _style(text, rng), th, "negative"

    low_theme, low_val = min(dims.items(), key=lambda kv: kv[1])
    high_theme, high_val = max(dims.items(), key=lambda kv: kv[1])
    # map broad dimensions to more specific themes now and then
    remap_neg = {"Work-life balance": ["Work-life balance", "Workload"],
                 "Career growth": ["Career growth", "Learning & development"],
                 "Recognition": ["Recognition", "Team collaboration"]}
    if low_val < 3.1 and (high_val < 4.1 or rng.random() < 0.6):
        theme = rng.choice(remap_neg.get(low_theme, [low_theme]))
        return _style(NEGATIVE[theme][rng.integers(len(NEGATIVE[theme]))], rng), str(theme), "negative"
    if high_val > 3.9:
        theme = rng.choice(remap_neg.get(high_theme, [high_theme]))
        return _style(POSITIVE[theme][rng.integers(len(POSITIVE[theme]))], rng), str(theme), "positive"
    theme = THEMES[rng.integers(len(THEMES))]
    return _style(NEUTRAL_THEMED[theme], rng), theme, "neutral"
