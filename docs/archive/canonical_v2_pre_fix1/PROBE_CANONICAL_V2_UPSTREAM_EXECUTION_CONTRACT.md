# ProBE canonical V2 — collaudo esecutivo dell'upstream

## Stato e riferimento

Questo documento accompagna il nuovo collaudo sintetico dell'upstream canonico.
Non attesta che il collaudo sia già stato eseguito su thanos.

Il riferimento acquisito è il checkpoint di migrazione del 17 ottobre 2026,
con `CANONICAL_V2_SNAKEMAKE_RECEIPTS_STATUS=PASS`: 150 regressioni PASS e cinque
regole scientifiche eseguite realmente tramite Snakemake su input sintetici,
dal workload già materializzato fino a Temporal V2. Restano acquisiti anche
l'audit dei 37 binding reali e i precedenti dry-run del DAG.

Lo snapshot fornito per questa ripresa è
`PROBE_V2_UPSTREAM_SNAPSHOT_20261017T125729Z_c2d73ec9.tar.gz`, SHA256
`19fc54adeb6317fff044512a474880a8e403ab543337360a90d4819797a8d362`.
I 333 file inclusi sono stati verificati contro il manifest dello snapshot.

Identità del repository di riferimento:

- percorso: `/data/PROGENY/ProBE-chatgpt`;
- branch: `chatgpt/probe-v2-canonical`;
- HEAD: `6d6e03b66e70b289b3fa200795becdd326b44e25`;
- file canonical V2 intenzionalmente non tracciati da Git al checkpoint.

Il nuovo marker finale è distinto da quello precedente:
`CANONICAL_V2_UPSTREAM_SNAKEMAKE_STATUS=PASS`.
Può essere emesso soltanto dopo le verifiche esecutive descritte qui.

## Confine esatto del collaudo

Il DAG `workflow/canonical_v2.smk` esistente parte da sorgenti di discovery e
di documentazione già preparate. Il nuovo collaudo rispetta questo confine:
genera piccoli sorgenti sintetici contrattualmente validi, poi lascia che
Snakemake produca tutti gli artefatti canonici a partire da essi.

| Gruppo | Esecuzioni produttive |
| --- | ---: |
| EXACT registry, candidate ledger, projection, lookup per START ed END | 8 |
| Documentary handoff, B3, complete release workload | 3 |
| Allineamento C++, resolver, M8, M10c, Temporal V2 | 5 |
| Totale produttori / receipt | 16 |
| Totale job del DAG, includendo `all` | 17 |

I primi 11 produttori costituiscono il nuovo tratto upstream. Gli ultimi
cinque consumano il workload appena prodotto: questa composizione verifica
l'integrazione nuova, senza riaprire l'audit del gate precedente.

La preparazione delle fixture usa i veri produttori disponibili per i
target, il contesto tassonomico 02B, la classificazione degli anchor, la
compilazione delle chiavi 04C, l'indice 04D1 e `merge_discovery`.
I piccoli input scanner 03 sono invece costruiti come dati sintetici.
Le tabelle 04D2, lineage pairs e rescue pairs richieste dal contratto sono
presenti con header corretti e senza osservazioni artificiali positive.

Non vengono preparati prima di Snakemake ledger, registry canonici,
projection, lookup, handoff, B3, workload o pubblicazioni downstream.
I bundle GO sorgente 01B0/01B1 sono fixture di ingresso: la compilazione
della policy GO usa il produttore reale e i manifest hanno hash coerenti.

Questo gate non certifica l'esecuzione originaria di tutti gli scanner
01–04 né elimina i binding 04 ancora richiesti dal DAG. La dipendenza dai
kernel 05C2 e 06A usati da B3 resta esplicita. Nessuna pubblicazione storica
05–08 è utilizzata come sorgente del percorso scientifico principale.

## Due difetti d'integrazione corretti

### Ruoli documentali nativi

Il compilatore 04D1 emette ruoli tipizzati quali
`END:ORDERED_LOCUS,START:ORDERED_LOCUS`. Il lookup conservava internamente
ruoli e origine nella stessa stringa; l'esportazione riversava il suffisso
di origine anche in `source_roles`, sebbene esistesse già la colonna
`source_key_origin`. Il lettore scientifico riconosceva soltanto le vecchie
forme artificiali `START_ANCHOR` / `END_ANCHOR`.

Le modifiche a `documentary_lookup.py` e `documentary_science.py` separano
l'origine dai ruoli e interpretano i ruoli nativi coerenti con il tipo di
chiave. Un ruolo tipizzato incompatibile viene respinto. Restano leggibili
i ruoli espliciti delle fixture precedenti. Non cambia lo schema TSV.

La correzione non trasforma le chiavi provenienti dagli EXACT in prove di
donazione: queste restano strumenti di ricerca. La corroborazione del locus
si applica soltanto alle osservazioni indipendenti ammesse dal contratto.

### Identità tassonomica delle aggiunte documentali

`release_workload._extras` lasciava vuoto `target_primary_bucket` nelle righe
aggiunte dal solo canale documentale. Il loader nativo del resolver richiede
quel campo non vuoto e pertanto non poteva consumare il workload completo.

La modifica a `release_workload.py` applica la formula effettiva di 02B:
`S:<species>` quando resolved TaxID e species TaxID coincidono, altrimenti
`T:<resolved>`. Il handoff ha già provato che target e candidato hanno lo
stesso resolved TaxID; la species è quella della stessa release, verificata
nei passaggi precedenti. TaxID mancanti o non canonici sono respinti.

Il validator del collaudo confronta inoltre il bucket osservato delle
aggiunte con il vero 02B generato per quel target e quella release.

Le due correzioni interessano tre moduli canonical V2 non tracciati. Non
cambiano workflow, manifest storici, policy pinnata, reader GO o kernel C++.

## Disegno sintetico e risultati attesi

Le release sono START `2023_01` ed END `2026_03`; la policy eseguita è
`same_species_exact`, quella del checkpoint. Entrambi i target hanno TaxID
111. Le sequenze sono di 20 residui e non condividono k-mer di lunghezza 5.

T1 conserva il query ID originale `P99901`, assente dai FULL. Possiede due
EXACT in ogni release: `P10001` same-TaxID e `P10002` same-species ma
cross-subtaxon. Quest'ultimo è FULL-only e senza GO; verifica quindi sia il
recupero dei metadati FULL sia l'indipendenza della discovery dalla copertura
GO. La comparsa tra i donor non implica che M8 debba trovare annotazioni.

Le chiavi disponibili soltanto negli EXACT END consentono di cercare in
START altri record: un gene letterale debole, una normalizzazione e due
record con lo stesso locus collidente. L'origine END e il carattere
retrospettivo della ricerca devono rimanere visibili. Nessuno dei quattro
record diventa donor; il caso normalized-only resta fuori dal workload.

Un NONEXACT cross-TaxID deliberatamente incluso negli input sintetici ammessi
raggiunge il workload, ma deve essere presente nello skip sidecar e assente
dall'output numerico C++. Questo è un controllo negativo del gate tassonomico,
non un'attestazione dell'esecuzione dello scanner originario.

T2 conserva `P20001`, non ha EXACT e non è presente nella discovery di
similarità. Il candidato `P20001` è NONEXACT e `FRAG=1` in entrambe le release;
raggiunge il workload attraverso il lookup documentale indipendente.
Gli anchor nativi START/END corroborano `ORDERED_LOCUS:NOEX_LOC`.
La continuità diretta di accession autorizza il donor; la stabilità del locus
viene verificata separatamente, così la continuità diretta non può nascondere
una regressione nella lettura dei ruoli 04D1.

Per questo scenario `partial_sequence=allow` e `documentary_fragment=allow`
sono espliciti nella sola configurazione sintetica. La configurazione
pinnata del progetto non viene modificata.

| Osservabile | START | END | Totale |
| --- | ---: | ---: | ---: |
| Record FULL | 8 | 3 | 11 |
| Coppie nel ledger iniziale | 3 | 2 | 5 |
| Coppie EXACT | 2 | 2 | 4 |
| Candidati lookup | 5 | 1 | 6 |
| Osservazioni lookup | 6 | 2 | 8 |
| Aggiunte al workload dal canale documentale | 4 | 1 | 5 |
| Coppie nel workload completo | 7 | 3 | 10 |
| NONEXACT inviate al DP | 4 | 1 | 5 |
| NONEXACT escluse per cross-TaxID | 1 | 0 | 1 |
| Normalized-only differite | 1 | 0 | 1 |
| Donor `same_species_exact` | 3 | 3 | 6 |

Il C++ esegue cinque coppie in entrambe le direzioni: 441 celle per
direzione e coppia, 4410 celle complessive. I quattro EXACT hanno
`OK_EXACT` e zero celle DP. Le righe finali 06C1 sono nove; il decimo candidato
resta nello skip e nelle decisioni complete del resolver.

La policy `strict` è usata nel runner soltanto per verificare che la discovery
mantenga lo stesso fingerprint e che il namespace donor cambi. Questo gate
non dichiara una seconda esecuzione scientifica completa con policy `strict`.

### GO e Temporal

Le annotazioni GAF sono costruite per release e mantengono tutti i 17 campi.
M8 deve recuperare soltanto le annotazioni dei donor ammessi. Le annotazioni
sentinella dei candidati bloccati non possono comparire nei suoi prodotti.
Gli assertion ID sono ricostruiti indipendentemente da release, SHA256 del
GAF e numero di linea. I RID sono confrontati con quelli dei FULL sintetici,
non soltanto fra un output e il successivo.

Il validator confronta integralmente la provenienza diretta SQL, le righe
term/code propagate, il TSV dei termini e le tabelle Temporal. Le attese
sono derivate dalla topologia GO dichiarata e dalle annotazioni sorgenti,
non dai risultati che devono verificare.

| Prodotto | Risultato atteso |
| --- | --- |
| M8 | 5 sorgenti per release, 10 link donor totali |
| M10c | 10 supporti diretti, 16 righe term/code, 14 termini distinti |
| Temporal EVAL | 4: due CONFIRMATION e due NOVEL |
| Temporal SKIP | 2: SCORE_START_ONLY |
| Temporal NEUTRAL | 1: CONFLICT_IKR_IRD |
| Radici GO escluse da Temporal | 2 |

La comparsa in START di un'annotazione disponibile soltanto in END, uno
scambio di RID o la sostituzione del query ID originale con quello del donor
devono produrre FAIL.

## Procedura del programma di esecuzione

Il programma autoestraente crea un percorso nuovo in `/data/PROGENY/tmp`.
Il payload contiene i nove file da installare e un manifest con checksum e
baseline. È disponibile anche l'opzione `--extract-only` per ispezionarlo.

1. Verifica HEAD, branch, hash dei piccoli sorgenti rilevanti dello snapshot
   e stato non tracciato dei tre moduli da correggere. I nuovi file devono
   essere assenti. Non verifica di nuovo i 37 payload scientifici reali.
2. Copia i sorgenti necessari in una directory isolata e sovrappone i nove
   file del pacchetto. Le CLI puntano a questa copia; il DP usa il binario
   C++ pinnato già presente nel repository originale.
3. Controlla l'ambiente esistente, incluso Snakemake 9.27.0; esegue Ruff sui
   soli Python del pacchetto con il profilo del progetto, correzioni sicure,
   formattazione e controllo finale. In staging classifica `probe` come
   first-party esplicitamente.
4. Esegue otto moduli pytest: 42 regressioni esistenti pertinenti ai confini
   modificati e 21 nuove regressioni. Richiede 63 PASS e zero skip, verificati
   anche nel report JUnit. Non rilancia la precedente suite completa di 150.
5. Esegue i 17 job del DAG in una destinazione nuova. Verifica i risultati
   scientifici, i manifest nativi e tutte le 16 receipt esterne.
6. Solo dopo il successo della copia isolata, salva i backup e installa i
   tre moduli corretti e i sei nuovi file. Registra gli hash effettivi dopo
   Ruff. Non crea commit e non cambia branch.
7. Verifica Ruff nel repository e ripete l'intero DAG sintetico con i file
   installati, in un secondo namespace nuovo. Controlla che sorgenti
   estranei alla patch, HEAD, branch e binario DP siano rimasti identici.
8. Conserva log, JSON, JUnit, fixture, pubblicazioni e sorgenti installati in
   un unico archivio `_REPORT.tar.gz`. Questo è il file da restituire in chat.

Se il collaudo fallisce prima dell'installazione, il repository non riceve
la patch. Se fallisce dopo, il programma ripristina soltanto i file gestiti,
verificando che i loro byte non siano stati modificati da altri nel
frattempo. Un conflitto è segnalato esplicitamente e non viene sovrascritto.
I backup e i risultati parziali restano disponibili nel percorso del gate.

Le operazioni di pubblicazione rimangono quelle native. Le receipt sono
esterne e contengono lo SHA256 dell'anchor reale. Stage07 e M8 usano
`validate_bundle(..., deep=True)` senza allentarne i controlli. M10c e Temporal
mantengono i propri manifest nativi, con controllo dei payload, dimensioni
e SHA256. Nessun bundle può contenere `.snakemake_timestamp` o receipt interne.

Il fingerprint del DAG non è un hash completo del codice: il nuovo namespace
di output è quindi obbligatorio dopo queste modifiche. Il programma non tenta
di riusare pubblicazioni precedenti.

Il marker `CANONICAL_V2_UPSTREAM_SNAKEMAKE_STATUS` descrive il collaudo
scientifico. `EVIDENCE_ARCHIVE_STATUS` descrive separatamente il confezionamento
del rapporto: un problema di archiviazione dopo un successo scientifico non
annulla quel risultato né provoca una ripetizione del DAG. In quel caso
restano il percorso delle evidenze e `gate_result.json`; l'uscita è 2.
L'uscita normale è 0; un fallimento del collaudo produce uscita 1.

## File installati

Modifiche a tre moduli esistenti:

- `src/probe/canonical_v2/documentary_lookup.py`;
- `src/probe/canonical_v2/documentary_science.py`;
- `src/probe/canonical_v2/release_workload.py`.

Sei file nuovi:

- `tests/test_canonical_v2_documentary_native_roles.py`;
- `tests/test_canonical_v2_documentary_workload_identity.py`;
- `tests/canonical_v2_upstream_fixture.py`;
- `tests/canonical_v2_upstream_go_fixture.py`;
- `tests/PROBE_V2_UPSTREAM_SNAKEMAKE_20261017.py`;
- `docs/PROBE_CANONICAL_V2_UPSTREAM_EXECUTION_CONTRACT.md`.

Il runner esplicito non viene raccolto automaticamente da pytest.

## Verifiche effettive prima della consegna e lavoro residuo

Sono stati letti i contratti e i produttori effettivi nello snapshot e sono
state svolte revisioni incrociate della fixture, dei validator e delle patch.
Il manifest del pacchetto riporta le verifiche locali effettive di sintassi e
gestione transazionale dei file.

Nell'ambiente di preparazione non erano disponibili rdflib, pytest, Ruff e
Snakemake. I tentativi di recuperare le dipendenze sono stati bloccati da
risposte HTTP 403. Non viene dichiarato alcun PASS locale di quei programmi
né del collaudo scientifico. Il gate è predisposto per essere eseguito con
l'ambiente già funzionante su thanos e si ferma prima dell'installazione se
le verifiche preliminari falliscono.

Dopo un autentico PASS del nuovo gate, risulterà collaudata l'intera
composizione canonica dal suo attuale confine d'ingresso fino a Temporal V2
su questo scenario sintetico. Restano distinti l'eventuale esecuzione
originaria degli scanner fuori da quel confine, l'esecuzione scientifica sui
dati reali, la misura delle prestazioni su scala reale e il consolidamento
Git. Nessuna di queste attività viene avviata da questo programma.
