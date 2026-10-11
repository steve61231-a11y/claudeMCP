# The Economist Style Guide — the rules this engine enforces

Extracted from the 2005 edition (The Economist / Profile Books), which the
client supplied. **This is an extract, not the book**: the prescriptive
sections `engine/reports/style_check.py` implements, kept here because this
repository is the only thing that survives a chat session, and a style guide
in an upload is one the next session cannot read.

Copyright The Economist Newspaper Ltd. Quoted here for internal reference in
the engine that applies it.

Where this engine deliberately does NOT follow the guide, the reason is in
`style_check.py` beside the exclusion and pinned by
`engine/tests/test_house_style_is_the_economist_s.py`. The short version:
five of the pairs below are wrong in the collocations this product writes —
"health facility" is the standard Kenyan term, a UK peer is a person, firms
exit markets, nobody "breaks" human rights, and "the following three issues"
is correct English — and `stakeholder` and `governance` are on the
horrible-words list while also being the name of a section and a framework
here. A check that fires on correct prose is one its reader learns to skip.

---

## Introduction — the six rules, from Orwell

On only two scores can The Economist hope to outdo its rivals consistently. One is the quality of its analysis; the other is the quality of its writing. The aim of this book is to give some general advice on writing, to point out some common errors and to set some arbitrary rules.
The first requirement of The Economist is that it should be readily understandable. Clarity of writing usually follows clarity of thought. So think what you want to say, then say it as simply as possible. Keep in mind George Orwell’s six elementary rules (“Politics and the English Language”, 1946):

		Never use a metaphor, simile or other figure of speech which you are used to seeing in print (see metaphors).
		Never use a long word where a short one will do (see short words).
		If it is possible to cut out a word, always cut it out (see
unnecessary words).
		Never use the passive where you can use the active (see grammar and syntax).
		Never use a foreign phrase, a scientific word or a jargon word if you can think of an everyday English equivalent (see jargon).
		Break any of these rules sooner than say anything outright barbarous (see iconoclasm).

Readers are primarily interested in what you have to say. By the way in which you say it you may encourage them either to read on or to give up. If you want them to read on:

Do not be stuffy “To write a genuine, familiar or truly English style”, said Hazlitt, “is to write as anyone would speak in common conversation who had a thorough command or choice of words or who could discourse with ease, force and perspicuity setting aside all pedantic and oratorical flourishes.”
Use the language of everyday speech, not that of spokesmen, lawyers or bureaucrats (so prefer let to permit, people to persons, buy to purchase, colleague to peer, way out to exit, present to gift, rich to wealthy, show to demonstrate, break to violate). Pomposity


and long-windedness tend to obscure meaning, or reveal the lack of it: strip them away in favour of plain words.

Do not be hectoring or arrogant Those who disagree with you are not necessarily stupid or insane. Nobody needs to be described as silly: let your analysis show that he is. When you express opinions, do not simply make assertions. The aim is not just to tell readers what you think, but to persuade them; if you use arguments, reasoning and evidence, you may succeed. Go easy on the oughts and shoulds.

---

## Short words

short words Use them. They are often Anglo-Saxon rather than Latin in origin. They are easy to spell and easy to understand. Thus prefer:
	about to approximately	plant to facility after to following	set up to establish
	but to however	show to demonstrate
	enough to sufficient	spending to expenditure
	let to permit	take part to participate
	make to manufacture	use to utilise
Underdeveloped countries are often better described as poor.
Substantive often means real or big. “Short words are best and the old words when short are best of all.” (Winston Churchill)

---

## Unnecessary words

unnecessary words Some words add nothing but length to your prose. Use adjectives to make your meaning more precise and be cautious of those you find yourself using to make it more emphatic. The word very is a case in point. If it occurs in a
sentence you have written, try leaving it out and see whether the meaning is changed. The omens were good may have more force than The omens were very good.
Avoid:
strike action (strike will do) cutbacks (cuts)
track record (record)
wilderness area (usually either a wilderness or a wild area) large-scale (big)
the policymaking process (policymaking) sale events (sales)
weather conditions (weather)
This time around means This time, just as any time soon means
soon. And at this moment in time means now or at present.
Shoot off, or rather shoot, as many prepositions after verbs as possible. Thus:

Companies can be bought and sold rather than bought up and sold off.
Budgets may be cut rather than cut back. Plots can be hatched but not hatched up.
Organisations should be headed by rather than headed up by
chairmen.

---

## Horrible words

horrible words Words that are horrible to one writer may not be horrible to another, but if you are a writer for whom no words are horrible, you would do well to take up some other activity. No words or phrases should be banned outright from appearing in print, but if you use any of the following you should be aware that they may have an emetic effect on some of your readers.
See also clichés.
carer – and most caring expressions
chattering classes facilitate famously governance
grow the business guesstimate
informed (as in his love of language informed his memos)
likely (meaning probably, rather than probable)

looking to (meaning intending to)
matériel ongoing poster child prestigious proactive
rack up (profits, etc) savvy
segue

---

## Jargon

jargon Avoid it. You may have to think harder if you are not to use jargon, but you can still be precise. Technical terms should be used in their proper context; do not use them out of it. In many instances simple words can do the job of exponential (try fast),
interface (frontier or border) and so on. If you find yourself tempted to write about affirmative action or corporate governance, you will have to explain what it is; with luck, you will then not have to use the actual expression.
Avoid, above all, the kind of jargon that tries to dignify nonsense with seriousness:
The appointee … should have a proven track record of operating at a senior level within a multi-site international business, preferably within a service- or brand-oriented environment
jib, gibe, gybe
	jib (noun)	sail or boom of a crane jib (verb)	to balk or shy
	gibe (verb)	to scoff or fiout gibe (noun)	taunt
	gybe (verb)	to alter course

---

## Journalese and slang

journalese and slang Do not be too free with slang like He really hit the big time in 1994. Slang, like metaphors, should be used only occasionally if it is to have effect. Avoid expressions used only by journalists, such as giving people the thumbs up, the thumbs down or the green light. Stay clear of gravy trains and salami tactics. Do not use the likes of, or Big Pharma (big drug firms).
And avoid words and expressions that are ugly or overused, such as:
In general, try to make your writing fresh. It will seem stale if it reads like hackneyed journalese. One weakness of journalists, who on daily newspapers may plead that they have little time to search for the apposite word, is a love of the ready-made, seventh- hand phrase. Lazy journalists are always at home in oil-rich country A, ruled by ailing President B, the long-serving strongman, who is, according to the chattering classes, not squeaky clean but a wily political operator – hence the present uneasy peace – but, after his recent watershed (or ground-breaking or landmark or sea-change) decision to arrest his prime minister (the honeymoon is over), will soon face a bloody uprising in the breakaway south. Similarly,
lazy business journalists always enjoy describing the problems of troubled company C, a victim of the revolution in the gimbal- pin industry (change is always revolutionary in such industries),

---

## Sloppy writing

slither, sliver As a noun, slither is scree. As a verb, it means slide. If you mean a slice, the word you want is sliver.

sloppy writing Use words with care.
If This door is alarmed, does its hair stand on end? If this envelope says Urgent: dated material, is it really too old-fashioned to be worth reading? Is offensive marketing just rude salesmanship?
More serious difficulties may arise with indicted war criminals. As their lawyers could one day remind you, these may turn out to be innocent people accused of war crimes.
A heart condition is usually a bad heart. A near miss is probably a near hit. Positive thoughts (held by long-suffering creditors, according to The Economist) presumably means optimism, just
as a negative report is probably a critical report. Industrial action
is usually industrial inaction, industrial disruption or a strike. A courtesy call is generally a sales offer or an uninvited visit. A

substantially finished bridge is an unfinished bridge. Someone with high name-recognition is well known. Something with reliability problems probably does not work. If yours is a live audience, what would a dead one be like?
And what is an ethics violation? An error of judgment? A crime? A moral lapse?
See also unnecessary words.
