# Compliance notes: US and global rules for a tool like Stackwise

*Our reading of the rules as of 4 October 2026. Not legal advice. Laws change; check the sources and ask a lawyer before a commercial launch.*

**Short version.** Stackwise is a static, read-only reference tool. It has no accounts, no cookies, no analytics and no personal data, and it uses public sources only. That keeps it outside most privacy and security regimes. The real risks are different: being mistaken for legal advice, wrong or stale rules, the licence terms of the sources, accessibility if a public body adopts it, and AI transparency. The table says what each rule means for us and what we already do.

## Privacy and data

| Rule | Does it apply? | What Stackwise does |
|---|---|---|
| **EU GDPR, UK GDPR, ePrivacy (cookie rules)** | Only when personal data is processed or cookies are set. Neither happens. | No accounts, cookies, analytics or third-party requests (fonts are embedded; a CSP blocks all outside connections). Typed facts stay in the browser tab. The web host (Vercel, GitHub Pages) keeps ordinary server logs like any host; a production launch should name it in a privacy notice. |
| **US state privacy laws** (California CCPA/CPRA and about twenty other states) | They cover businesses that collect personal information above set thresholds. | We collect none. Parcel facts are public records (address, year built, units); no owner names. |
| **CalOPPA** (California) | Requires a privacy policy when a site collects personal information from Californians. | None collected. A one-paragraph privacy note is cheap insurance for a launch (the README and app footer already say what we do not collect). |
| **COPPA** (children under 13) | Applies to services directed at children or that knowingly collect their data. | Not directed at children; nothing collected. |
| **Other countries** (Canada PIPEDA and Quebec Law 25, Brazil LGPD, and similar) | Same logic: they apply when personal data of residents is processed. | Same answer: none is. |

## Consumer protection and legal-services rules

| Rule | Does it apply? | What Stackwise does |
|---|---|---|
| **FTC Act section 5 and state unfair-practice laws** | Yes, to any public claim. | No claim of perfect accuracy. Every screen says "not legal advice", that summaries are written by AI, and that no lawyer reviewed them. "Unknown" is shown instead of a guess. |
| **Unauthorized practice of law** (set by each US state) | This is the main legal risk. States separate legal *information* from legal *advice*. | We show what public laws say, which ones may reach a building, and the exact source. We give no tailored advice or documents, and point people to the source and to counsel. A lawyer in each target state should review the wording before a real launch. |
| **Fair housing laws** (federal Fair Housing Act and state laws) | They bind housing providers and those who screen tenants. | Stackwise makes no decision about any person and scores no tenant. Keep it that way. |
| **Antitrust and algorithmic pricing** | Relevant background: several cities ban algorithmic rent-setting. | Stackwise sets no prices. It reports where those bans apply. |

## AI rules

| Rule | Does it apply? | What Stackwise does |
|---|---|---|
| **EU AI Act, Article 50 (transparency)** | In force since 2 August 2026. The omnibus delay only gives generative systems placed on the market earlier until 2 December 2026 for machine-readable marking ([Goodwin](https://www.goodwinlaw.com/en/insights/publications/2026/08/alerts-technology-dpc-eu-ai-act-transparency-obligations-now-in-force)). High-risk duties (Annex III) were pushed to 2 December 2027. | AI-written text is labelled on every screen. It is a reference lookup that evaluates no person, which we read as not high-risk. |
| **Colorado AI Act (SB 24-205)** | Aimed at high-risk AI that makes consequential decisions, housing included. Originally due 30 June 2026, enforcement was stayed and the law is in litigation and flux ([Squire Patton Boggs](https://aihub.squirepattonboggs.com/2026/05/the-colorado-ai-act-hits-a-wall-litigation-legislative-uncertainty-and-an-enforcement-standstill/)). | We make no consequential decision about a consumer. Check the current status before any launch in Colorado. |
| **California AI transparency and training-data laws** | Aimed at model developers and very large providers. | Not us. The model provider carries those duties. |
| **Model provider terms** | Anthropic's usage policy asks for human oversight and AI disclosure for high-risk, consumer-facing uses such as legal guidance ([Anthropic](https://www.anthropic.com/news/usage-policy-update)). | AI use is disclosed. Code checks every quote against the source, conflicts are flagged for a person to review, and the README says plainly that no lawyer has reviewed the output. For real public use, add a human review step for new or changed rules. |
| **NIST AI RMF, ISO/IEC 42001** | Voluntary frameworks. Useful if a customer asks. | Our design follows the same ideas: documented data, human review of conflicts, logged model answers, tests. |

## Security and accessibility

| Rule | Does it apply? | What Stackwise does |
|---|---|---|
| **Security frameworks** (NIST SP 800-53 and FedRAMP for federal work, SOC 2 or ISO/IEC 27001 for enterprise buyers, OWASP guidance) | No law requires them for a static information page. Buyers may ask. | One static file. A CSP that blocks all connections, no third-party code, every string escaped before display, links open with `rel="noopener"`, the API key only in a git-ignored `.env`, a test that scans the repository for keys, CI on every push. |
| **ADA Title III** (businesses open to the public) | Courts differ on whether websites are covered. | See next row; accessibility was built in and checked. |
| **ADA Title II web rule** (state and local governments) | Requires WCAG 2.1 AA for their web content, including content offered through contractors. After the April 2026 extension: 26 April 2027 for entities of 50,000 or more people, 26 April 2028 for smaller ones ([Reed Smith](https://www.reedsmith.com/articles/doj-extends-digital-accessibility-compliance-dates-under-title-ii-of-the-ada/)). | Matters if a city or housing authority adopts the tool. axe-core finds zero WCAG 2.x A/AA violations in English and Spanish, light and dark, desktop and phone. Automated checks do not prove full conformance; a manual audit is still needed. |
| **EU Accessibility Act, Section 508** | Cover certain consumer services in the EU and federal procurement. | The same WCAG work applies. |

## Sources and licences

- **Statutes are not copyrightable** in the US (government edicts doctrine; *Georgia v. Public.Resource.Org*, 2020), but some code publishers' sites forbid automated copying in their terms. We saved those pages by hand (the model reads them afterwards), keep short excerpts, link the original, and skipped any page behind a login or robot check.
- **Parcel data** comes from public assessor datasets. Check each dataset's licence before a commercial launch.
- **Fonts** are open source (SIL OFL), included with their licences in `web/fonts/`.

## Before a real launch

1. A lawyer reviews the disclaimers and the unauthorized-practice question in each target state.
2. Add a short privacy notice and name the host.
3. Run a manual accessibility audit (keyboard, screen reader).
4. Add a human review queue for new and changed rules, and a schedule for re-reading sources.
5. Confirm the licence of every data source.
6. Re-check the AI rules above: several changed during 2026.
