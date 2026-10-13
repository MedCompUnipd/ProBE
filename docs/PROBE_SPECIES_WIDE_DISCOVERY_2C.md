# ProBE / PROGENY — Decisione scientifica e contratto Stage 2C

Data: 2026-10-13. Stato: specifica approvata concettualmente dall'utente;
integrazione software **non ancora applicata**. Non riscrivere stage pubblicati.

## Obiettivo

Per ogni target con tassonomia risolta, cercare proteine UniProt in **tutti**
i TaxID appartenenti alla specie del target, inclusi strain e sottospecie.
La scelta `strict` / `same_species_exact` per i donor avviene **solo dopo**
la discovery e non determina il suo universo di ricerca. La stessa discovery
pubblicata e i medesimi upstream sono condivisi fra tali policy downstream.

## Contratto scientifico del trasferimento GO

1. Candidato e target con lo **stesso TaxID risolto**: conservare le regole
   documentarie validate per la stessa entità tassonomica. Accession e storia
   del record, lineage e geometrie possono ammettere anche sequenze diverse,
   secondo le policy già esplicite; nessuna ammissione per pura similarità.
2. **TaxID risolti differenti ma stessa specie verificata**:
   - `strict`: candidato conservato nella discovery, ma **nessun GO donor**
     attraverso questa relazione cross-strain.
   - `same_species_exact`: GO donor possibile solo con identità letterale
     dell'intera sequenza aminoacidica, stessa lunghezza e zero differenze;
     nessuna continuità documentaria può compensare un mismatch fra strain.
     Preservare gli altri filtri di eleggibilità e di provenienza.
3. **Specie differenti**: nessuna nuova ammissione, anche con identità completa.
4. Tassonomia irrisolta/incongruente: non applicare la deroga cross-strain.
5. UniProt `FRAG=1`: non promuovere automaticamente da sola similarity/identity;
   valgono i controlli documentari e le policy frammenti concordate.
6. Identità di sequenza: verificare l'intera stringa, non solo un hash o una
   percentuale. Residui ambigui devono essere resi espliciti nell'audit;
   una corrispondenza `X` contro `X` non dimostra identità biologica.
7. Target FASTA con header arbitrario: il nuovo canale non deve richiedere
   accession UniProt del target.
8. GOA START e END restano temporalmente distinti; niente anticipazione END
   a START. Classificazione SCORE/NON_SCORE/EXCLUDED invariata.

## Scelta architetturale consigliata

**Non modificare il binario v1 congelato né la semantica di `rank_aware`.**

- Nuova discovery Stage 03 V2 species-wide: riutilizzare i bucket
  tassonomici di specie dello Stage 02B per **tutti** i target risolti,
  inclusi quelli classificati sotto il rango di specie. Conservare i
  candidati trovati tramite k-mer sia esatti sia non esatti, con TaxID
  target/candidato e provenienza. Il resolver filtra poi i donor.
  Un nuovo binario/policy versionati mantengono intatti scanner v1 e
  pubblicazioni canoniche. Applicare l'universo species-wide anche
  al percorso Stage 05R1 per i suoi target di rescue.
- Per garantire che la discovery species-wide non perda exact hits
  assenti dal GO_POSITIVE_V2 e dal rescue selettivo, aggiungere un canale
  di *exact sequence discovery* su FULL UniProt START/END. Usare snapshot
  tassonomico della release, indice con chiave logica
  `(species_taxid, sequence_length, sequence_digest)` e verifica letterale.
  Gli exact hits sono disponibili senza accession del target e vengono
  conservati con RID, TaxID, sequenza, provenienza e flag FRAG.
- Preservare i candidati legacy e le relazioni documentarie per lo stesso
  TaxID. Unire per tripla `(release, target_id, rid)` con provenienza
  multipla (species-wide GO positive, FULL exact, rescue, lineage),
  senza perdere audit o introdurre finti anchor documentari.
- I candidati cross-strain non esatti restano nel report discovery, ma
  non devono generare donor né, ove il contratto lo consenta, pesanti
  allineamenti globali senza scopo. Mantenerne comunque il conteggio e
  la ragione dell'esclusione in diagnostica.
- Adattare le fasi che generano evidenza/associazioni e i contratti degli
  allineamenti per exact hits; nessuna simulazione di evidenza documentaria.
  Non si deve segnare `direct_accession` solo per identità di sequenza.
- Nuovo resolver scientifico versionato, con taxonomy join verificato
  indipendentemente prima di decidere il GO donor.
- Fingerprint indipendenti: `discovery_key` include input release, target,
  snapshot tassonomici, algoritmo, codice e parametri della discovery;
  `resolver_key` include `discovery_key` e donor policy; M8/M10c/M12/
  diagnostica dipendono transitivamente dal resolver corretto.
- Pubblicazione atomica in directory nuove immutabili. In caso di
  `strict -> same_species_exact`, riusare discovery e le evidenze indipendenti
  dalla policy; ricalcolare solo Stage 07 e discendenti necessari.

## Vincoli già osservati nel repository

- Lo scanner C++ `probe_primary_candidate_scan_v1.cpp`, con `rank_aware`,
  assegna un bucket di specie solo a target già al rango di specie;
  sotto la specie usa il bucket del TaxID risolto. Non è species-wide
  per i target strain.
- Stage 03 esplora il GO-positive V2; Stage 05R1 esplora FULL V2 solo
  per i target selezionati come uncovered. Una nuova ricerca esatta
  species-wide su FULL non è equivalente a cambiare un singolo flag di 03;
  occorrono sia discovery ampliata sia integrazione con gli altri canali.
- `stage07_variants.py` oggi include SHA di 05A2 e 06C1 canonici e il runner
  Stage 07 ne verifica le directory canoniche. Richiede una generalizzazione
  esplicita per accettare upstream di variante verificati; non disattivare
  i controlli di provenienza per aggirare il problema.
- Audit passato: 638 coppie `PRIMARY` cross-strain con sequenza esatta
  (438 START, 200 END); 632 non donor, 6 donor nel modello legacy.
  La nuova semantica `strict` non deve essere attribuita retroattivamente
  ai run storici. Separatamente, 24 promozioni 2B da audit documentario
  potrebbero includere sequenze non identiche: non ereditarne
  automaticamente l'eligibilità nel nuovo modello exact-only.

## Test di accettazione obbligatori

1. Target strain A e candidate strain B, stessa specie, sequenza intera
   identica: presente nella discovery; `strict -> NO`,
   `same_species_exact -> YES` (salvo altri guard).
2. Come sopra, un mismatch/indel o diversa lunghezza:
   `same_species_exact -> NO`, anche con evidenza documentaria.
3. Stesso TaxID, sequence variant con direct anchor verificato:
   conservare l'attuale comportamento documentario.
4. Specie diversa e sequenza identica: `NO`.
5. Candidato `FRAG=1` e tassonomia ignota: nessuna promozione automatica.
6. Due record di ceppi diversi con lo stesso digest ma sequenza
   effettivamente diversa: rifiuto al controllo letterale.
7. Multipli RID/accession per la stessa sequenza: mantenere provenienza,
   deduplicare soltanto l'identità di coppia release-target-RID.
8. Target con header FASTA arbitrario; TaxID merged/alias risolti;
   target al rango di specie e sotto il rango di specie.
9. Cambiare solo donor policy: zero job Stage 03/indice/05A2/06C1;
   solo nuovo Stage 07 e downstream. Cambiare snapshot/input/codice
   upstream: fingerprint differente e rebuild isolato.
10. Manifest canonici e bundle pubblicati identici prima/dopo,
    niente sovrascritture, test regressivi legacy inalterati.
11. Ruff: max 88 caratteri per riga per Python; prima del delivery
    lint completo secondo la configurazione repo (`E501`, `SIM102`,
    `B905`, ecc.). `pixi run python3`; scoped Ruff fix/format/check,
    test Stage e suite completa, `git diff --check`.

## Nota sulla scala di elaborazione

La discovery species-wide per k-mer può moltiplicare le coppie candidate.
Una nuova indicizzazione di FULL UniProt potrà richiedere una lettura
one-shot consistente (circa 199 GB complessivi per le due release attuali).
Prima di avviarla, controllare indici esistenti, footprint, budget RAM/I/O,
possibile moltiplicazione dei RID esatti fra ceppi e spazio disponibile.
`strict` e `same_species_exact` devono riutilizzare lo stesso indice e la
stessa discovery pubblicata. Nessuna scansione GOA aggiuntiva per progettare
questa variazione.

## Confine di implementazione

Questa specifica non equivale a codice già integrato. L'implementazione
richiede un nuovo scanner/index, merge candidato, provenienza/verifica,
resolver versionato, fingerprint e DAG Snakemake. È scorretto presentare
come completata la funzionalità solo dopo avere modificato il filtro
`taxonomy_policy` dello Stage 03 C++ o il solo `ResolverPolicy`.
