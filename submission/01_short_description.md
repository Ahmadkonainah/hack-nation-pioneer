Stackwise answers one question: which rental housing laws apply at this address, on this date?

Landlords, tenants and housing counselors face a patchwork of state statutes, city ordinances, pending bills and court rulings that keep changing. We built a pipeline that reads public law and turns it into 56 structured rules. Each rule has a citation, a source link and the exact words it came from. Claude Sonnet 5.5 does the reading against a fixed schema. Code then checks that every quote is in the source and works out from the dates whether a rule is in force, not yet effective, pending or failed.

For each of the 500 sample addresses, Stackwise places the address in its state, county and city with the Census geocoder and tests every rule against year built and unit count. The answer is applies, superseded, not yet effective, pending or unknown, with a plain-English reason. When parcel data cannot say, for example a missing year built, the answer is unknown and not a guess. When a stricter local law governs, the state rule shows as superseded and points to the one that governs. Where a state law may displace city ordinances, as with the New Jersey FAIR Act, the rule is flagged for a human reviewer and not decided.

Change tracking runs the same engine on any two dates. California's AB 325 starts on 1 January 2026 and changes answers for 250 addresses. New Jersey's FAIR Act lands in July 2027 for 140. The Massachusetts rent-control ballot question that was struck down changes nothing for anyone.

Everything uses public sources, every screen says it is not legal advice, and adding a city means adding its pages and re-running. A full run costs a few dollars.

(290 words)
