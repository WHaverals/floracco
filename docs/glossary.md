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

Related phrases in the corpus include **disdetta parziale**, **disdetta rescissa**, and **disdire in tronco**.

### Modifica

A modification to an existing accomandita, such as a change in terms, parties, capital, duration, or related conditions.

### Cessione

Transfer or assignment of a share, capital interest, action, or right in a partnership. Often appears together with disdetta or modifica.

### Bilancio

Balance or accounting statement. In the corpus this may refer to settling accounts, reporting profits/losses, or recognizing capital/account balances, not only to a modern balance sheet.

See also **stralcio / stralciario**, since several later records concern balances of terminated firms or winding-up processes.

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

> **FT review pending:** Confirm whether this should map to `LP` in the database/input rules and to "limited partner" in English-language documentation.

Capital-providing partner or investor. In modern terms, roughly the limited partner. Use with care: the historical role should be checked against the wording of each record.

### Accomandatario

> **FT review pending:** Confirm the best project definition and whether this should map to "general/managing partner" in English-language documentation.

Managing or operating partner. In modern terms, roughly the general/managing partner with responsibility for administration. In the corpus this appears alongside roles such as **institore**, **complimentario**, and firm names.

### GP

> **FT review pending:** Confirm whether `GP` in the original input rules should always be understood as **accomandatario** / general or managing partner.

Database/input abbreviation for general partner. Appears in the schema field "contract type" as one of the values `GP/LP`.

### LP

> **FT review pending:** Confirm whether `LP` in the original input rules should always be understood as **accomandante** / limited partner.

Database/input abbreviation for limited partner. Appears in the schema field "contract type" as one of the values `GP/LP`.

### Compagno / compagni

Partner(s), often in a company or firm. Common in firm names and narrative descriptions.

### E compagni / & C.

> **FT review pending:** Confirm how this should be represented in firm names and whether it should be treated only as part of a name or also as a data-quality flag.

Literally "and partners/company." The input rules include a field for `& C?` with the note "i.e. e compagni."

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

### Causidico

> **FT review pending:** Confirm whether this should be glossed as legal representative, advocate, or another project-specific role.

Historical legal role appearing in the corpus. External references define *causidico* as someone who represented parties in legal matters; use cautiously until reviewed.

### Proxy

> **FT review pending:** Confirm the project rule for recording proxies, especially the exception for women acting as proxies.

Database/input concept for someone acting on another person's behalf. The schema note says proxy names are normally not entered, except when the proxy is a woman; in that case, the woman's name is entered rather than the names of those on whose behalf she acts.

### Eredi

Heirs. Important because capital interests, disdette, and renewals may involve heirs rather than the original contracting party.

### Eredi di / ed eredi

> **FT review pending:** Confirm the analytical distinction between "heirs of" (`Eredi di`) and "and heirs" (`ed eredi`).

The input rules distinguish `heirs of?` ("Eredi di") from `& heir(s)?` ("ed eredi"). Keep these separate until reviewed.

### Vedova

Widow. Important for interpreting gender, legal capacity, kinship, and capital ownership.

### Guardian / tutor

> **FT review pending:** Confirm how to define and code women acting as guardians/tutors of children, and how this relates to **mundualdo / mondualdo**.

The schema includes `is guardian?` and `guardian of`, with the note to enter the names of the children of whom a woman is a tutor.

### Tutore / tutrice

> **FT review pending:** Confirm how this differs from **curatore / curatrice**, **guardian / tutor**, and **mundualdo / mondualdo**.

Guardian or tutor, often acting for minors or heirs.

### Curatore / curatrice

> **FT review pending:** Confirm project-specific meaning and whether this should be coded separately from **tutore / tutrice**.

Curator, guardian, or administrator acting for another person or estate. The term appears especially in contexts involving women, minors, heirs, and patrimonies.

### Commesso

> **FT review pending:** Confirm whether this means commissioned agent, appointed representative, employee, or another role depending on context.

Person acting by commission or appointment. In the corpus, a commesso may act on behalf of another person or firm.

### Ministro / ministri

> **FT review pending:** Confirm project-specific meaning. Do not interpret as a modern political minister.

Business manager, agent, or operating representative in some firm contexts.

### Economo

> **FT review pending:** Confirm whether this should be understood as steward/administrator of a patrimony.

Administrator or steward, often of a patrimony or estate.

### Donzello

> **FT review pending:** Confirm whether this needs a full definition or only a note as an office/status label in witness contexts.

Office or status label that appears frequently among witnesses and court/administrative personnel.

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

### Cancelliere / notaio cancelliere

> **FT review pending:** Confirm the best institutional definition and how to describe cases where absent parties are represented "via cancelliere."

Chancery official or notary-chancellor recording acts in the registers. The corpus often notes that absent parties act "via cancelliere" or that the cancelliere "fa le veci" of an absent party.

### Presente / assente

> **FT review pending:** Confirm whether presence/absence should be treated as a data-quality or legal-status feature in reconciliation work.

Indicates whether a party was present or absent at the act. Often paired with phrases such as "presente e accettante" or "assente, via cancelliere."

### Fa le veci / via cancelliere

> **FT review pending:** Confirm how this should be interpreted legally and whether it should affect database coding.

Formula indicating that someone, often the cancelliere, acts in place of or represents an absent party.

### A margine

Marginal note. Often used to point to a related disdetta, declaration, or later act.

### In calce

At the foot or end of an act. Often used for signatures or notes.

### Libro antecedente / presente libro / libro susseguente

References to the previous, current, or subsequent register/book. These are important for tracing related acts across volumes.

### Atto

Formal act or recorded legal act. In reconciliation work, avoid assuming that every act is a new main contract.

### Scritta privata / scrittura privata / scritta sociale

> **FT review pending:** Confirm whether these should be treated as source documents distinct from the register entry and how they should be cited in reconciliation.

Private or social written agreement underlying a registered entry. The Word narratives frequently refer to terms being contained in a private writing signed by the parties.

### Chirografo

> **FT review pending:** Confirm the best project definition and whether this should be grouped with private writings or treated separately.

Signed written instrument. External references define *chirografo* as a document written or signed by the party assuming an obligation.

### Filza / giustificazioni

> **FT review pending:** Confirm whether these should be defined as archival containers, supporting files, or project-specific source categories.

Terms appearing in references to supporting documentation, e.g. "filza di giustificazioni."

### In bianco / manca il giorno / non indicato / illeggibile

Editorial and source-state markers for missing, unspecified, blank, or unreadable information. Preserve these markers in derived data and issue inventories.

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

## Commercial, Legal, and Formulaic Language

Terms in this section occur frequently in the Word narratives and can affect how contracts, subcontracts, roles, and economic activities are interpreted.

### Ragione / ragione cantante

> **FT review pending:** Define the project meaning and decide whether to translate as firm, business, account, company, or something context-dependent.

Recurring term for a business, account, firm, or commercial concern. The phrase **ragione cantante** often appears with a firm name or "sotto nome di..." formula.

### Negozio

> **FT review pending:** Confirm whether the glossary should translate this broadly as business/commercial operation rather than shop.

Business, trade, shop, or commercial operation. The phrase appears in many economic-activity descriptions, e.g. "negozio d'arte di seta," "negozio mercantile," or "negozio di grossiere."

### Interesse

> **FT review pending:** Confirm how to distinguish this from **azione**, **rata**, **capitale**, and database investment fields.

Interest or stake in a business or accomandita. Do not assume it means modern interest on a loan.

### Interessato capitalista

> **FT review pending:** Confirm whether this should be treated as a role label, a descriptive phrase, or both.

Phrase used for a capital-interested party or investor.

### Azione

> **FT review pending:** Confirm whether this should be translated as share, action, stake, right, or context-dependent.

Share, right, or interest in an enterprise. Avoid mapping directly to a modern corporate share unless confirmed.

### Benefizio di accomandita

> **FT review pending:** Define the legal meaning and relationship to limited liability/protection in the Florentine records.

Phrase indicating that a business, society, or interest operates with the benefit of accomandita.

### A beneplacito

> **FT review pending:** Confirm whether this should be coded as unspecified/open duration or as termination at the discretion of one or more parties.

Formula indicating that an arrangement lasts at the pleasure/discretion of one or more parties.

### In tronco

> **FT review pending:** Confirm whether this means immediate termination, termination without ordinary notice, or another technical meaning.

Phrase occurring with disdetta or facoltà of termination.

### Stralcio / stralciario

> **FT review pending:** Confirm meaning in these records: winding-up period, liquidation/settlement, accounting closure, or role in closing affairs.

Term associated with winding up, settling, or closing a business/account. **Stralciario** appears as a person handling such settlement work.

### Sinistro accidente / liberi tutti

> **FT review pending:** Confirm whether this formula should be treated as a standard limited-liability clause and how it should be coded.

Recurring formula explaining that in case of adverse accident/event, parties intend to be liable only for capital, profits, and gains, and to enjoy privileges and immunities granted by relevant statutes. Several Word files include a legend equating "in caso di sinistro accidente" with "liberi tutti."

### Sicurtà

> **FT review pending:** Confirm whether to translate as surety, security, guarantee, or context-dependent.

Security or surety language, often connected to credit, borrowing, or obligations.

### Mallevadoria

> **FT review pending:** Confirm project usage and whether this should be grouped under surety/guarantee terms.

Guarantee or suretyship term. Appears in restrictions on obligating a business or providing security.

### Cambio / pigliare a cambio

> **FT review pending:** Confirm whether this should be glossed as exchange, borrowing at exchange, credit operation, or context-dependent.

Credit/exchange operation. Contracts sometimes prohibit or restrict "pigliare a cambio" without permission.

### Cassa

Cash box, treasury, or cash account. Appears in accounting/balance contexts.

### Utili / perdite

Profits and losses. Often relevant in bilancio and settlement entries.

### Conto / conti

Account(s). Use context to distinguish bookkeeping accounts from narrative summaries.

## Economic Activity Terms

These entries are a first reviewable vocabulary mined from recurring Word-narrative phrases. They should not yet be treated as a controlled economic-activity taxonomy; `docs/data_dictionary.md` should eventually document the database fields and accepted coding rules.

### Arte di seta / setaiolo / setaioli

Silk trade, silk business, or silk workers/merchants. One of the dominant economic activities in the corpus.

### Arte di lana / lanaiolo / lanaioli

Wool trade, wool business, or wool workers/merchants. One of the dominant economic activities in the corpus.

### Lana in Garbo / lana di Garbo

> **FT review pending:** Define this precisely; do not normalize simply to generic wool without review.

Recurring wool-sector phrase, likely a specific branch or quality/category within the wool trade.

### Banco / compagni di banco

> **FT review pending:** Confirm whether to translate as bank, banking house, banking business, or context-dependent.

Banking or banking-house activity.

### Mercatura

Trade or commerce, often broad and sometimes paired with **cambi**.

### Cambi

Exchange/credit operations. See also **cambio / pigliare a cambio**.

### Fondaco

> **FT review pending:** Confirm whether this means trading house, warehouse, shop, or a more specific Florentine commercial category.

Commercial establishment or trading context. Appears alone and in combinations such as **fondaco e grossiere**.

### Grossiere

> **FT review pending:** Confirm the best English rendering and whether this corresponds to grocer, wholesaler, dealer in colonial/dry goods, or a broader category.

Recurring trade label in phrases such as "negozio di grossiere" and "fondaco e grossiere."

### Spezieria / speziale

Apothecary, spice, or drug trade. Often appears with **drogheria**.

### Drogheria / droghiere

Drug/spice/grocery trade. Often paired with **spezieria**.

### Pizzicagnolo / pizzicheria

> **FT review pending:** Confirm the historical sense and best English label.

Provision/food seller or related shop/trade.

### Merciaio / merceria

> **FT review pending:** Confirm historical sense and whether this should be treated separately from **lanciaio**, **calzettaio**, and other small-goods trades.

Mercer or dealer in small goods/wares.

### Lanciaio

> **FT review pending:** Define this trade; appears often enough to require controlled interpretation.

Trade label often appearing with **grossiere** or **merciaio**.

### Calzettaio

Stocking-maker or stocking seller. Appears in combinations such as "merciaio e calzettaio."

### Battiloro / battilori

Gold-beater(s), a craft/business term.

### Tiraloro / tiralori

> **FT review pending:** Confirm relation to **battiloro** and whether to code together or separately.

Gold/silver-thread or metal-drawing craft term appearing with **battiloro**.

### Quoiaio / cuoiaio / concia

> **FT review pending:** Confirm spelling variants and whether leather-related trades should be grouped.

Leatherworker/leather trade and tanning/curing activity. Appears in phrases such as "negozio ... di quoiaio" and "concia."

### Saponaio

Soap-maker or soap seller.

### Stamperia / libreria / libraio

Printing, bookselling, or book trade.

### Fornaio / mugnaio

Baker and miller.

### Calzolaio

Shoemaker.

### Brigliaio

> **FT review pending:** Confirm whether this should be translated as bridle-maker/seller or a broader saddlery trade.

Trade involving bridles or related equipment.

### Valigiaio

> **FT review pending:** Confirm exact historical meaning.

Maker or seller of valigie/travel goods.

### Velettaio

> **FT review pending:** Confirm exact historical meaning.

Trade label occurring in the corpus; likely connected to veils or textile accessories.

### Calderaio

Coppersmith or maker/seller of cauldrons/metal vessels.

### Orefice

Goldsmith.

### Linaiolo

Linen/flax trade or linen merchant.

### Salumaio

Seller of cured meats/provisions.

### Macelleria

Butchery/meat trade.

### Appalto

> **FT review pending:** Confirm whether this should be translated as contract, concession, farming/lease of a public revenue or service, or context-dependent.

Contracted concession or farming/lease arrangement. Appears in activities such as public contracts, tesoreria, paper, meat seal, and related administrative/business operations.

### Condotta

> **FT review pending:** Confirm meaning in economic-activity contexts.

Conducting/contracting/management activity; appears in phrases such as "condotta e mercatura."

### Fiere

Fairs or fair-related trading circuits. Novi and Piacenza recur in the corpus.

## Project and Database Bridge Terms

These entries come from the original database input rules (`docs/schema/DB structure and input rules.xls`). They connect historical language in the Word narratives to structured database fields. Full field definitions belong in [data_dictionary.md](data_dictionary.md).

### Firm name / sotto nome di

> **FT review pending:** Confirm whether "sotto nome di..." should be treated as the primary evidence for the database firm-name field.

The input rules define firm name as what the documents usually indicate with the phrase "sotto nome di..."

### Discretion

> **FT review pending:** Confirm the preferred label and interpretation for this concept in English documentation and data-quality checks.

Database/input flag used when the document explicitly allows some breadth in place or activity. The schema gives examples such as "e in ogni altro luogo" for place and "et altre cose" for economic activity.

### Economic activity attributed by database

> **FT review pending:** Confirm when inferred economic activity should be allowed and how such inference should be documented.

Database/input flag marked `YES` when no explicit economic activity is given in the contract but one can be inferred from the contract.

### Automatic renewal

> **FT review pending:** Confirm how automatic renewal relates to **rinnovo**, **proroga**, and the `months of renewal` fields.

Database/input flag used when a contract indicates that, unless termination is given before expected expiration, the contract is renewed.

### Administrators

> **FT review pending:** Confirm how this field should relate to roles such as **accomandatario**, **institore**, **procuratore**, and named managers.

Database/input flag marked `YES` when someone other than one of the general partners is mentioned as managing or co-managing the partnership, or when the GP name differs from the firm name.

### Subcontract

> **FT review pending:** Confirm whether "subcontract" is the best English label for later acts tied to a main contract.

Database grouping for follow-up acts used to complement or correct a main contract, for example names, residence, end date, renewal, variation, or firm-name changes.

### Balance / renewal / termination / variation

> **FT review pending:** Confirm how these database-normalized subcontract types should map to narrative terms such as **bilancio**, **rinnovo**, **disdetta**, **modifica**, **cessione**, **proroga**, and **ratifica**.

Controlled input labels for subcontract type in the schema. These are database categories and may not map one-to-one onto the Italian bracket tags in the Word narratives.

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
