# ProBE Stage 08: audit del codice e contratto proposto

**Stato: proposta di integrazione, non implementazione production e non nuova policy approvata.**

## Base documentale e perimetro

Sorgenti esaminati: `stage08_logica_annotazioni_sdeRCZ.txt`, raccolti in
`/data/PROGENY/ProBE-chatgpt`, branch `chatgpt/pre-resolver-v2`, commit `5e982a7`.
Il resoconto contiene i corpi completi di records, parsing/gaf, evidence,
knowledge, preprocessing, comparison, target_mapping, relations, truth e masking.
La lista dei test contiene soltanto i percorsi, non i loro corpi o nuovi risultati.

Il contratto Stage 07 -> Stage 08 già discusso rimane il riferimento per i donor:
tutte le associazioni YES, separate per release, senza nuova entity resolution,
senza richiedere FASTA e senza equiparare associazione a equivalenza completa
fra prodotti. Gli Stage 01-07 e i loro bundle non sono stati modificati.

## Conclusione

Le policy delle evidenze e diverse primitive ontologiche sono riutilizzabili.
Il confronto attuale non è invece un ingresso diretto adatto ai donor Stage 07:
la sua mappa globale alias -> exact sequence non rappresenta le associazioni
molti-a-molti, dipendenti dalla release e dal target, richieste dal nuovo livello.

La prima implementazione deve essere un livello additivo di recupero e
attribuzione delle righe annotate. Confronto temporale, propagazione negativa,
truth e masking non devono essere attivati implicitamente durante l'estrazione.

## Risultati dell'ispezione

### 1. Evidenze: preset disponibili, default da rendere esplicito

`EvidencePolicy.experimental_strict()` accetta i sei codici sperimentali
tradizionali. `experimental_all()` include anche gli high-throughput.
`experimental()` è un preset di compatibilità che include entrambi; questo è il
default di `compare_annotations()` quando la policy non viene passata.

`accepts()` verifica l'esistenza di almeno un codice ammesso: non sceglie un solo
codice rappresentativo. `DirectTermState` conserva l'insieme delle evidenze e
le asserzioni sorgente. Questa impostazione va preservata.

### 2. Identificatori: incompatibilità con l'adattamento diretto

`comparison._identity_lookup()` restituisce `subject_to_sequence: dict[str, str]`.
Un alias associato a due sequence ID diversi produce `AMBIGUOUS_SEQUENCE_ALIAS`.
La mappa non contiene release, donor eligibility, database, rid o target-context.
La stessa mappa viene utilizzata per entrambi gli snapshot.

Conseguenza: non usare l'unione dei donor START/END come semplice IdentityMap.
Non ricostruire identità da FASTA e non inserire fittiziamente target_id nei campi
sequence_id per eludere il contratto di quelle API.

L'aggregazione Stage 08 deve partire da `(release, target_id)` mantenendo ogni
associazione `(release, target_id, rid)`. Due target distinti non acquisiscono
il diritto di condividere annotazioni per la sola uguaglianza del sequence hash.

### 3. NOT: veto anticipato e non distinto per donor

`comparison._assertions()` raccoglie i NOT per `(sequence_id, aspect)`, prima della
selezione per profilo di evidenza. Chiama `ontology.excluded_by_not()` ed esclude
sia le righe NOT dal positivo sia i positivi colpiti dal veto.

Il veto non distingue accession donor, relazione annotativa, estensione o forma
del prodotto. Un NOT con IEA può quindi escludere un positivo con IDA proveniente
da un altro donor, anche sotto un profilo sperimentale stretto.

Questo comportamento è effettivamente implementato, ma non può essere importato
implicitamente come nuova policy cross-donor di Stage 08. Conservare entrambe
le polarità e produrre eventuali segnalazioni, senza cancellazioni automatiche.

Il corpo reale di `GeneOntology.excluded_by_not()` non è incluso in questo
resoconto: le relazioni e il percorso interno della propagazione negativa non
sono stati convalidati da questa analisi. Le microprove usano NOT sul medesimo
termine e non richiedono ipotesi sulla discesa ontologica.

### 4. Chiusura strutturale != accumulo delle evidenze

`RelationPolicy.conservative()` ammette alla propagazione gli IRI esatti di
is_a e part_of e separa archi cross-aspect e altri archi navigabili.
`inclusive_ancestor_closure()` conserva termini sorgente, antenati, distanze e
un percorso testimone. Non legge gli evidence code dei DirectTruthTerm.

L'accumulo delle evidenze va costruito con un join tracciabile dalla relazione
termine diretto -> antenato alle asserzioni del target/release. Occorrono insiemi
distinti di supporti diretti e propagati; lo stesso codice può appartenere a
entrambi. I supporti vanno identificati dalla riga sorgente e dal collegamento
al donor, non moltiplicati per il numero dei percorsi ontologici.

Il campo `relation_iris` della closure raccoglie le relazioni dei percorsi minimi;
non include necessariamente le relazioni di percorsi ammessi più lunghi. Non
pubblicarlo come inventario di tutte le relazioni presenti su tutti i percorsi.

### 5. Eventi: selezione preliminare, non eligibility finale

`classify_direct_events()` produce quattro EventType: evidence_upgrade,
redundant_ancestor, specificity_refinement e branch_acquisition.
Un evidence_upgrade ha `qualifies_for_benchmark=True` in questo livello.
`ComparisonResult.selected_targets` include quindi anche target con soli upgrade.

`TruthSelectionProfile.combined()` seleziona soltanto acquisizione e refinement;
`evidence_confirmation()` seleziona invece gli upgrade con una policy di maschera
separata. L'eligibility finale richiede il profilo scelto e una truth valutabile
non vuota. Non usare selected_targets come lista definitiva del benchmark.

Un cambio di soli riferimenti/contesto resta nei vecchi e nuovi DirectTermState,
ma non genera un record dedicato in changes o events quando i codici restano
uguali. Un report esplicito dei context-only changes richiede un prodotto derivato.

La classificazione degli eventi usa le evidenze dirette: non va reinterpretata
silenziosamente come confronto delle evidenze già accumulate sugli antenati.

### 6. Scala: non lanciare il costruttore attuale sul corpus senza adattamento

`construct_truth_and_masks()` scansiona nuovamente le collezioni globali di
asserzioni ed eventi per ogni sequence/aspect. Le closure ricostruiscono
l'adiacenza e `m` materializza un ampio universo valutabile per ogni risultato.

Sono osservazioni statiche sulla struttura del codice, non misure di runtime/RAM.
La versione per il corpus richiede indici per unità annotativa/aspetto, riuso
dell'adiacenza, cache delle chiusure per termine e una rappresentazione compatta
di Q condiviso e delle esclusioni specifiche del target.

### 7. Altri limiti circoscritti

- `NeutralTruthPolicy.include_non_scored_curated` è dichiarato, ma non governa
  `accepts()` o la selezione dei neutral_records. I due preset correnti lo
  impostano entrambi a True: il problema emerge come opzione non applicata
  quando viene richiesto False, non come errore dimostrato dei run canonici.
- `compare_annotations()` richiede ancora lo stesso oggetto GeneOntology per i
  due snapshot. Il fingerprint è passato dal chiamante; non viene calcolato o
  verificato contro i due oggetti da questa funzione.
- `AGENTS.md` contiene riferimenti al branch `codex/modifiche-main` e a un modello
  exact-sequence-first. Il log e il contratto Stage 07 descrivono il lavoro
  corrente su `chatgpt/pre-resolver-v2`. Va documentato il confine, non eseguito
  un cambio di branch o una risoluzione ex novo.
- `preprocessing.py` e `target_mapping.py` non sono entry point per Stage 08:
  lavorano anche sui FASTA e svolgono compiti upstream già completati.

## Contratto additivo proposto per il primo modulo

### Input

1. Bundle Stage 07: accepted associations, selection metadata e manifest.
2. GAF normalizzati START e END con i rispettivi manifest Stage 01B1.
3. Provenienza della policy comune Stage 01B0; nessuna nuova normalizzazione.
4. Anagrafica target già preparata, se si produce copertura anche dei target
   senza associazioni accettate. Nessuna lettura dei FASTA.

### Modello logico

- **Asserzione sorgente:** release, origine GAF normalizzato, fingerprint del
  file, numero di riga in quel GAF e contenuto annotativo. Conservare i campi
  originali del normalizzato, incluso il qualificatore/relazione raw.
- **Associazione donor:** la riga Stage 07 resta riferita alla sua chiave
  `(release, target_id, rid)`; non riclassificare YES/NO.
- **Collegamento annotativo:** associazione Stage 07 -> asserzione sorgente.
  Lo stesso record GOA può avere più collegamenti a target diversi.
- **Vista diretta per target:** `(release, target_id, aspect, GO, polarity)`
  con tutti gli evidence code e i riferimenti ai supporti.
- **Copertura:** conteggi di righe positive/NOT e stato per ogni associazione;
  riepilogo per target/release distinto dalla futura eligibility.

Una chiave di lookup annotativo include la release e il namespace del database:
non attribuire a UniProt una riga di un altro database soltanto perché l'accession
è scritto nello stesso modo. Non espandere alias o eliminare suffissi.

### Stati descrittivi proposti

- `ROWS_RETRIEVED`: almeno una riga annotativa conservata, positiva o NOT.
- `NO_RETAINED_GOA_ROWS`: donor YES senza righe nel normalizzato interrogato.
- `DONOR_DISABLED`: associazione accettata con donor NO; nessun trasferimento.
- `NO_ELIGIBLE_DONOR`: riepilogo di un target/release senza donor YES.

I nomi sono proposti, non congelati. Nessuno di questi stati equivale ad assenza
biologica della funzione, negatività GO o categoria NK/LK già assegnata.

### Invarianti

- Considerare tutti i donor YES, non un best hit.
- Tenere START ed END separati e accettare soltanto i collegamenti deliberati
  nella rispettiva release.
- Preservare evidenze, polarità e supporti completi. Nessun filtro sperimentale,
  veto NOT, propagazione o masking durante il recupero.
- Non associare donor NO a righe annotate; conservarli nell'audit.
- L'assenza di righe nel normalizzato non revoca l'accettazione Stage 07.
- Due target con la stessa sequenza non vengono fusi automaticamente.
- Il GO nella sorgente è già normalizzato. Non inventare un GO pre-normalizzazione
  o un numero di riga del GAF grezzo che non è disponibile.
- Se il GAF viene prefiltrato in streaming, conservare il numero della riga del
  normalizzato completo: il numero riassegnato nel subset non lo sostituisce.

### Test minimi per il primo incremento

1. Due donor dello stesso target: entrambi contribuiscono.
2. Un donor per più target: tutti i collegamenti vengono mantenuti.
3. Un donor ammesso solo in END non contribuisce a START.
4. Donor NO: nessun trasferimento, associazione conservata nell'audit.
5. Donor YES privo di GO: stato esplicito, nessuna annotazione inventata.
6. Donor solo-NOT: recuperato, non classificato come assente.
7. Positivo e NOT sullo stesso GO: entrambi conservati, evidenze separate.
8. Codici uguali con riferimenti diversi: un codice nel riepilogo, due supporti.
9. Target distinti con hash uguale e donor diversi: nessuna contaminazione.
10. Database diverso con accession uguale: nessun join non autorizzato.
11. Forma del prodotto e qualifier conservati anche con relazioni diverse.
12. Provenienza delle righe stabile rispetto al prefiltraggio del corpus.

## Validazione locale effettivamente eseguita

Sono stati estratti e verificati sintatticamente i dieci moduli del resoconto.
Sono state eseguite undici microprove sulle funzioni estratte, con oggetti
sintetici sostitutivi per ontologia, snapshot, identity map e validation report.
Queste prove verificano il flusso delle funzioni, NON il loader OWL reale, la
propagazione negativa reale, i dataset biologici o la suite del repository.
Un esito PASS significa che il comportamento descritto è stato riprodotto,
non che quel comportamento sia approvato per Stage 08.

1. **evidence_profiles**: I preset strict/all distinguono HDA; IMP pregresso impedisce un falso primo upgrade verso IDA.
2. **evidence_aggregation**: L'aggregazione corrente conserva IDA e IMP e le due asserzioni sorgente.
3. **one_to_many_alias**: Un alias collegato a due sequence ID produce AMBIGUOUS_SEQUENCE_ALIAS.
4. **release_unqualified_lookup**: La mappa globale A/B ammette B anche in START: non applica autonomamente il vincolo START=A, END=B.
5. **cross_donor_not**: Un NOT/IEA da B-2 esclude il positivo IDA da A-1 prima del filtro sperimentale.
6. **context_only_change**: Un cambio del solo riferimento resta nei contesti ma non genera changes/events.
7. **profile_event_counts**: HDA -> IDA genera upgrade con strict, non con experimental_all.
8. **structural_not_evidence_closure**: Sostituire IDA con IEA lascia identico il ClosureResult strutturale.
9. **closure_relation_scope**: Una relazione part_of su un percorso più lungo non entra in relation_iris del nodo raggiunto con un percorso minimo is_a.
10. **early_vs_final_selection**: Il target IEA -> IDA compare in selected_targets, ma non ha truth nel profilo combined.
11. **neutral_flag_not_enforced**: include_non_scored_curated=False non impedisce accepts({IBA}); i preset correnti usano True.

## Passo successivo

Implementare soltanto il lettore delle associazioni e la proiezione delle
asserzioni sui target, con i test sintetici sopra. Rinviare l'adattamento delle
API temporali e truth/masking a un incremento successivo, dopo avere fissato
la semantica dei contrasti NOT, delle evidenze dirette/propagate e dei profili.
Nessuna nuova ricognizione generale degli Stage 01-07 è necessaria per questo
primo incremento. I test esistenti e il corpo delle API ontologiche andranno
letti in modo mirato prima di adattare quelle specifiche primitive.
