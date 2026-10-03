**PROPOSED MIT AI HACKATHON CHALLENGE - 24 HOURS - PUBLIC DATA ONLY** 

# **Rental Housing Law Navigator** 

An AI system that reads public housing law and answers one question for any apartment address: which rules apply here today, and how do the supplied change cases affect the answer? 

**How might AI turn thousands of pages of state and local housing law into accurate, cited, address-level answers that renters, advocates, housing agencies and housing providers can trust?** 

## **The problem is specific, real and getting harder** 

Rental housing in the United States is regulated in layers: state statutes, county rules and city ordinances, each with its own coverage tests, effective dates and exemptions. The answer to what rules apply to an apartment depends on the exact address and the query date. 

|**Why it matters**|**What teams must handle**|
|---|---|
|**Layered rules**|State and local rules can overlap, defer to one another or be superseded.|
|**Address precision**|A mailing city is not always the legal city. Jurisdiction resolution is part of the task.|
|**Changing law**|The supplied tests include effective-date changes, pending bills and a failed ballot question.|



## **Who this helps** 

|**Renters**|**Advocates and agencies**|**Housing providers**|
|---|---|---|
|**Know which protections apply at their address.**|See how rules cover buildings and what supplied<br>changes would affect.|Understand obligations before acting, especially<br>without legal staff.|



**The challenge in one line: In 24 hours, build a working system that extracts rules from a provided corpus of real housing law, resolves sample addresses to the rules that apply with citations, and reports the address sets affected by five supplied change cases.** 

REALPAGE | DISCUSSION DRAFT | OCTOBER 2026 

1 

**WHAT TEAMS BUILD** 

# **From legal text to an address-level answer** 

Three required modules plus stretch goals. The starter pack lets teams spend the 24 hours on AI, workflow design and responsible reasoning rather than data collection. 

|**1. Extract**|**2. Resolve**|**3. Apply**|**4. Explain**|**5. Track**|
|---|---|---|---|---|
|**Turn statutes and**|Geocode an address and|Test coverage conditions|Return applicable rules with|Run the five supplied change|
|**ordinances into structured**|build its state, county and city|against building facts and|plain-language explanations|cases and report affected|
|**rule records.**|stack.|exemptions.|and citations.|addresses.|



## **Required modules** 

|**Module**|**Requirement**|
|---|---|
|**A - Rule extraction**|Read each corpus document and output one structured record per rule. Extraction must be automated, not hand-coded.<br>Capture category, jurisdiction, requirement, coverage conditions, exemptions, effective date, status, penalty, citation and a<br>quoted span.|
|**B - Address lookup**|Resolve each address to its legal jurisdiction stack and report every applicable rule. If coverage depends on a missing fact,<br>report unknown rather than guessing.|
|**C - Change tracking**|Run the supplied change cases, list affected sample addresses and support an as-of-date query. No surprise document or<br>mid-event release is required.|



## **Stretch goals** 

- Renter-facing plain-language view in English and Spanish. 

- Confidence indicator and conflict flag for each answer. 

- Extend to one new jurisdiction using the same extraction and coverage pipeline. 

- Audit view showing the source, retrieval date, as-of date and reasoning boundary for each answer. 

REALPAGE | DISCUSSION DRAFT | OCTOBER 2026 

2 

**SCOPE FOR 24 HOURS** 

# **3 states - 10 cities - 6 rule categories** 

Small enough to finish, varied enough to be hard. The jurisdictions cover state-only rules, layered local rules and recent or failed changes. 

|**State**|**Cities in scope**|**Why it is included**|
|---|---|---|
|**California**|Los Angeles, San Francisco, San Diego,<br>Berkeley, Santa Ana|Dense layering: statewide rent and just-cause rules, local rent control and<br>algorithmic-pricing rules.|
|**New Jersey**|Jersey City, Hoboken, Newark|City-by-city rent control, statewide eviction and screening rules, and a statewide<br>algorithmic-pricing law with a future effective date.|
|**Massachusetts**|Boston, Cambridge|State law bars local rent control; pending algorithmic-pricing bills and a failed ballot<br>question test negative findings.|



## **Rule categories** 

|**Category**|**What to capture**|
|---|---|
|**Rent increase limits**|Cap formula, covered buildings, exemptions and local-versus-state precedence.|
|**Just-cause eviction**|Allowed causes, notice, relocation assistance and coverage.|
|**Security deposits**|Maximum, exceptions and effective date.|
|**Application and screening fees**|Fee caps, allowed upfront charges, receipts and refunds.|
|**Screening restrictions**|Criminal-history and income-source limits and timing rules.|
|**Algorithmic rent-setting**|Covered software, prohibited conduct, penalties and effective date.|



The corpus covers three states and ten cities. It has not been reviewed by counsel and is for prototype challenge scoping only. 

REALPAGE | DISCUSSION DRAFT | OCTOBER 2026 

3 

**TEST CASES AND STARTER PACK** 

# **Deterministic change cases, packaged data** 

Teams work from the same fixed corpus and address sample. Change tracking is evaluated against five explicit cases, all defined at kickoff. 

|**Test**|**What a correct system does**|
|---|---|
|**T1 - CA AB 325 / SB 763**|Reports not yet effective on 2025-12-31 and applies on 2026-01-02 for California addresses.|
|**T2 - Hoboken and Jersey City local bans**|Applies each local ban only inside its own city limits, not in Newark.|
|**T3 - NJ FAIR Act**|Reports not yet effective on 2026-10-01, applies on 2027-07-02 and flags possible conflicts with local bans.|
|**T4 - MA S.2983 and H.5222**|Reports both bills as pending and lists the Massachusetts addresses they would affect if enacted.|
|**T5 - MA rent-control ballot question**|Reports no rent cap for Boston or Cambridge because the question was struck; affected set is empty.|



## **Starter pack provided to teams** 

|**Item**|**Contents**|**Format**|
|---|---|---|
|**Law corpus**|87 source documents with URL and retrieval date; links for sources without supplied text.|Text + CSV|
|**Sample addresses**|About 500 multifamily properties in nine cities from public assessor data. No owner names.|CSV|
|**Rule schema**|Required fields for every rule record and a worked example.|JSON Schema|
|**Change tests**|The five explicit cases above and their test definitions.|JSON|



REALPAGE | DISCUSSION DRAFT | OCTOBER 2026 

4 

**DATA AND RESPONSIBLE DESIGN** 

# **Build for transparency, not legal verdicts** 

The system should make housing law easier to see and understand without pretending to be a lawyer or helping anyone work around the rules. 

## **Public sources teams may use** 

|**Source**|**Use it for**|
|---|---|
|**State codes and municipal codes**|Official statute and ordinance text.|
|**LegiScan and Open States**|Bill status, sponsors, events and text where available.|
|**Census Geocoder and TIGER/Line**|Address-to-jurisdiction resolution and boundary checks.|
|**Public parcel data**|Building facts such as year built, units and use code.|
|**Local program lookups**|Spot-checks for rent-control coverage; not a replacement for the corpus.|



|**The solution should**|**The solution must not**|
|---|---|
|**Cite source text and retrieval date; show an as-of date; separate enacted**|Present output as legal advice or a compliance certification; invent rules or|
|**from pending law; say unknown when facts are missing; flag conflicts for**<br>**human review; keep an audit log.**|citations; use non-public data; or scrape sites in violation of their terms.|



## **A practical 24-hour plan** 

|**Time**|**Focus**|
|---|---|
|**Hours 0-1**|Kickoff, starter-pack walkthrough and mentor introductions.|
|**Hours 1-6**|Rule extraction against the supplied corpus and starter-pack self-check.|
|**Hours 6-11**|Geocoding, jurisdiction stacks and coverage logic.|
|**Hours 11-18**|Address lookup, citations, plain-language view and deterministic change cases.|
|**Hours 18-23**|Validation run, fixes and demo preparation.|
|**Hours 23-24**|Demos and judging.|



REALPAGE | DISCUSSION DRAFT | OCTOBER 2026 

5 

**BUILD AND SUBMIT** 

# **Make every answer traceable** 

The participant guide contains the full detail. These are the build expectations and submission materials to prepare for the event. 

|**Build expectations**|**Submission package**|
|---|---|
|**Build Modules A, B and C from the supplied corpus and address sample.**|rules.json: structured rules with citations and quoted source text; lookups.json:|
|**Extraction must be automated. Unknown is valid when facts are missing.**|results for all sample addresses; changes.json: affected addresses and conflict|
|**Use public data only, respect source terms and label every interface not**<br>**legal advice.**|flags for each supplied test; live demo and one-page method note.|



## **A strong submission** 

- Shows the source document, quoted span, retrieval date and as-of date for every answer. 

- Separates enacted, pending, not-yet-effective and failed changes. 

- Reports unknown when a required fact is missing and flags conflicts for human review. 

- Keeps a simple audit log so another person can reproduce the result. 

**The intended outcome: show that responsible AI can make housing law visible at the level that matters - a single address - so renters know their rights, providers know their obligations and every answer is traceable to its source.** 

REALPAGE | DISCUSSION DRAFT | OCTOBER 2026 

6 

