# Glossary

Working glossary for historical, archival, and project terms in the Florentine Accomandite Corpus. For database tables and fields, see [data_dictionary.md](data_dictionary.md).

Definitions here are meant to prevent obvious misreadings during documentation, notebook work, and reconciliation. Entries marked **FT review pending** should be checked by Francesca Trivellato before they are used (e.g., as controlled vocabulary in notebooks, queries, or database updates).

## Contract and Event Types

### Accomandita

A limited-partnership-style commercial arrangement. In modern Italian legal language, a *società in accomandita* has managing/general partners and capital-providing limited partners; historical Florentine practice should be interpreted from the records themselves. See also **accomandante** and **accomandatario**.

### Nuova / nuovo

A new accomandita registration. In the Word narratives this often appears as `[Nuova]` or `[Nuovo]`, followed by a contract number and narrative summary.

### Disdetta

Termination, cancellation, or formal notice ending an accomandita or an interest in it. Do not treat every disdetta as a new contract.

### Modifica

A modification to an existing accomandita, such as a change in terms, parties, capital, duration, or related conditions.

### Cessione

Transfer or assignment of a share, capital interest, action, or right in a partnership. Often appears together with disdetta or modifica.

### Bilancio

Balance or accounting statement. In the corpus this may refer to settling accounts, reporting profits/losses, or recognizing capital/account balances, not only to a modern balance sheet.

### Rinnovo

Renewal of an existing accomandita.

### Proroga

> **FT review pending:** Confirm how this should be distinguished from **rinnovo**, **conferma**, and **continuazione** in project coding.

Extension of the duration of an existing arrangement. Related to **rinnovo**, but kept separate because the records use both terms.

### Ratifica

Ratification or confirmation of an act already made, or made by another party or proxy.

### Conferma / continuazione

> **FT review pending:** Confirm whether these should remain separate event types or be grouped with **rinnovo** / **proroga** in analysis.

Confirmation or continuation of an existing arrangement. These should be interpreted in context and not automatically collapsed into rinnovo or proroga.

## People and Roles

### Accomandante

Capital-providing partner or investor. In modern terms, roughly the limited partner. Use with care: the historical role should be checked against the wording of each record.

### Accomandatario

> **FT review pending:** Confirm the best project definition and whether this should map to "general/managing partner" in English-language documentation.

Managing or operating partner. In modern terms, roughly the general/managing partner with responsibility for administration. In the corpus this appears alongside roles such as **institore**, **complimentario**, and firm names.

### Compagno / compagni

Partner(s), often in a company or firm. Common in firm names and narrative descriptions.

### Socio / soci

> **FT review pending:** Confirm how this term should be distinguished from **compagno**, **accomandante**, **accomandatario**, and database investor roles.

Partner(s) or members of a company. Do not assume this maps one-to-one to a database role such as `investor_id`.

### Interessato / interessati

Interested party or stakeholder in a firm, account, or accomandita.

### Institore

> **FT review pending:** Confirm project-specific historical usage before using this as controlled vocabulary.

A person placed in charge of running a commercial business, branch, or line of business. Modern legal usage treats an institore as a manager or representative with broad authority for the assigned business.

### Complimentario / complementario

> **FT review pending:** Define from the corpus before using this term analytically.

Appears in the corpus in phrases such as "complimentario della ragione." Likely a role connected to representing or completing a firm/account relationship, but the precise project definition is uncertain.

### Procuratore

Proxy or agent acting under authorization or mandate for another person.

### Eredi

Heirs. Important because capital interests, disdette, and renewals may involve heirs rather than the original contracting party.

### Vedova

Widow. Important for interpreting gender, legal capacity, kinship, and capital ownership.

### Mundualdo / mondualdo

Male legal guardian or authorizer for a woman in certain legal acts. The term derives from Lombard-law traditions and appears in early material. **Needs historical review** before being used analytically.

### Quondam / fu

Indicates that a named person is deceased, especially in patronymics or kin descriptions, e.g. "del quondam Marco" or "del fu Giovanni."

## Archival and Documentary Terms

### ASF

Archivio di Stato di Firenze.

### Mercanzia

> **FT review pending:** Add a fuller historical definition of the institution and its relevance to these registers.

The Florentine merchants' tribunal/chancellery context for most earlier registers.

### Camera di Commercio / CC

Later institutional series appearing in files such as `CC 1262`, `CC 1263`, and `CC 1263bis`.

### Registro / volume / libro

Register, volume, or book containing recorded accomandite.

### Carta / c. / cc.

Folio or leaf reference. `c. 1r` means carta 1 recto; `c. 1v` means carta 1 verso; `cc.` means multiple carte.

### Recto / verso (`r` / `v`)

Front and back side of a folio leaf.

### Rubrica

Alphabetical index of names, often attached to or separate from a register. The corpus notes often record whether the rubrica is ordered by given name, surname, or another convention.

### Estratto

> **FT review pending:** Confirm whether this should mean extract, index, register summary, or different things depending on context.

Extract or index-like summary. In register descriptions, often connected to rubrics or lists of names and references.

## Money and Accounting

### Capitale

Capital invested, assigned, or recorded in an accomandita.

### Rata

> **FT review pending:** Confirm whether this should be coded as share, installment, portion, or context-dependent.

Share, installment, or portion. The exact meaning may vary by record and should be interpreted in context.

### Ducato / ducati

> **FT review pending:** Confirm whether the glossary should include historical ranges/conversions or only basic labels for currency terms.

Currency unit. Often appears with conversion language, for example "ducati ... di moneta di lire 7 per ducato."

### Scudo / scudi

> **FT review pending:** Confirm whether the glossary should include historical ranges/conversions or only basic labels for currency terms.

Currency unit, frequent in later records.

### Fiorino / fiorini

> **FT review pending:** Confirm whether the glossary should include historical ranges/conversions or only basic labels for currency terms.

Currency unit, especially important in earlier records.

### Lira / lire; soldo / soldi; denaro / denari

> **FT review pending:** Confirm whether the glossary should include historical ranges/conversions or only basic labels for currency terms.

Units of account or currency subdivisions. Many entries specify mixed amounts.

### Moneta

> **FT review pending:** Confirm whether to define this only generally or include project rules for recording conversion phrases.

Money or currency. Include conversion context when recording amounts.

## Editorial and Data-Quality Terms

### `[sic]`

Marks an apparent error in the original source or transcription that should not be silently corrected.

### `[?]` and uncertain readings

Marks uncertainty. Preserve these markers; do not resolve them without source review.

### Track changes

Word editorial history. The Word narratives are authoritative and may contain unmerged corrections, deletions, insertions, or comments.

### Standardization

Project process of choosing a consistent form for names, places, or activities while preserving the original evidence.

### Reconciliation

Aligning Word narratives, SQLite records, and images so changes are traceable to source files and reviewed before database updates.

### Issue inventory

A machine-readable list of known data anomalies or review items, such as zero dates, name variants, duplicate people, or missing links. Intended to be regenerated and compared over time.
