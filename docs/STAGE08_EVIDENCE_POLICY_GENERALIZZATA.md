# ProBE Stage 08 - Evidenze configurabili e versionate

**Revisione 1.1 - 10 ottobre 2026.**

**Stato: addendum progettuale aggiornato con le decisioni confermate dall'utente.**
**Nessuna implementazione production o modifica degli Stage 01-07.**

Sono confermati la configurabilità delle evidenze, la separazione dei donor per
release e il ricalcolo limitato allo stage modificato e alle sue dipendenze a
valle. La matrice della sezione 8 definisce il comportamento richiesto, non una
funzionalità Stage 08 già implementata o validata.

Questo documento integra `STAGE08_AUDIT_E_CONTRATTO_PROPOSTO.md`.
La richiesta dell'utente conferma la necessità di scegliere liberamente gli
evidence code, estendere le categorie e mantenere donor distinti per START ed
END. I nomi di file, campi, stati e API qui proposti non sono ancora congelati.

## 1. Motivazione verificata nel codice

Nel resoconto `stage08_logica_annotazioni_sdeRCZ.txt`, raccolto dal branch
`chatgpt/pre-resolver-v2` al commit `5e982a7`:

- `AnnotationRecord.evidence` è una stringa; il parser verifica che non sia vuota,
  senza richiedere appartenenza a una lista chiusa.
- `EvidenceCategory`, `EvidenceTier`, `EVIDENCE_TIERS` ed `EVIDENCE_CATEGORIES`
  sono attualmente definiti nel codice Python.
- `EvidencePolicy.tier()` esclude intrinsecamente NAS e ND e non assegna tier ai
  codici non registrati nelle costanti.
- Le policy positive di START e neutrali di END richiamano a loro volta queste
  classificazioni fisse. Generalizzare soltanto il filtro degli eventi non basta.
- Il fingerprint delle evidenze attuale non rappresenta un registro esterno di
  definizioni e mapping: il nuovo percorso dovrà identificarlo esplicitamente.

Sono osservazioni sui sorgenti forniti, non il risultato di nuovi test eseguiti
sul repository. I preset esistenti restano riferimenti di compatibilità.

## 2. Tre livelli indipendenti

### 2.1 Osservazione delle evidenze

La raccolta annotativa conserva il codice esatto della riga del GAF normalizzato,
la polarità, il contesto e tutti i collegamenti Stage 07 autorizzati.
Non richiede un registro aggiornato per accettare un codice non vuoto mai visto.
Un campo mancante o una riga malformata rimangono errori di input, non nuovi codici.
Nessuna conversione implicita di maiuscole, alias o suffissi dei codici.

Durante la scansione si produce un inventario dei codici osservati. Per ciascun
ruolo e codice, l'inventario dei donor può riportare righe positive/NOT,
aspetti, donor distinti, target interessati e riferimenti sorgente esemplificativi.
Il conteggio delle righe sorgente è distinto dal conteggio dei collegamenti ai
target: una riga condivisa da due target non diventa due righe GOA.

Lo scope deve essere dichiarato: `retrieved_donor_assertions` oppure
`whole_normalized_gaf`. Il secondo può essere un censimento opzionale dei codici
realizzato nella stessa scansione, senza attribuire ai target righe di altri donor.
Un codice non osservato nel campione o nei donor non è per questo deprecato.

### 2.2 Registro delle definizioni

Un `EvidenceRegistry` caricato da un file locale versionato descrive i codici.
Può contenere codice, descrizione, gruppi/tag, stato documentato, identificatori
ECO opzionali, fonte, versione e motivazione di eventuali aggiunte dell'utente.
I gruppi sono identificatori configurabili, non membri obbligatori di un Enum.
Uno stesso codice può appartenere a più gruppi, senza ambiguità nella selezione
per unione di insiemi.

Le classificazioni ufficiali importate e i gruppi operativi dell'utente sono
informazioni separate. Un gruppo personalizzato non riscrive la definizione
ufficiale. Un codice osservato ma non descritto viene marcato come non classificato,
non assegnato automaticamente a sperimentale, elettronico o inaffidabile.

Il GAF fornisce il codice osservato; non contiene una tassonomia completa delle
evidenze né una graduatoria di affidabilità. L'eventuale importazione automatica
di definizioni usa fonti GO/ECO dichiarate e congelate. L'importatore produce un
registro locale verificabile: non modifica la policy del benchmark durante il run.
ECO, se usato, è una risorsa separata dal `go.owl` della gerarchia funzionale.
La conversione dettagliata da codice/contesto a ECO resta opzionale: non è
necessario caricare tutto ECO per supportare la selezione esplicita di codici.

### 2.3 Selezione per scopo

Una `EvidenceSelectionPolicy` seleziona insiemi di codici per uno scopo preciso:

- evidenze positive ammesse per gli eventi del benchmark;
- evidenze positive ammesse per la conoscenza pregressa K0;
- evidenze positive ammesse per la componente neutrale END;
- eventuali evidenze ammesse per vincoli NOT, quando quella fase sarà definita.

Queste policy sono indipendenti. Escludere IEA dagli eventi sperimentali non lo
esclude automaticamente da K0. La configurazione dei NOT non abilita da sola
alcun veto né stabilisce la compatibilità tra donor o contesti.

## 3. Compilazione deterministica della selezione

La selezione può partire da un preset versionato, da gruppi configurati o da
codici espliciti. Non è obbligatorio utilizzare gruppi o tier numerici.

```text
allowed_codes =
    (expanded_preset UNION expanded_include_groups UNION include_codes)
    MINUS exclude_codes
```

Le esclusioni prevalgono sulle inclusioni. Ogni decisione resta spiegabile.
Un riferimento a un gruppo inesistente è errore. Un codice esplicito sconosciuto
al registro richiede un'aggiunta documentata al registro: non viene ignorato o
promosso automaticamente. Un codice valido registrato ma non osservato nei dati
può essere selezionato e produce un avviso informativo, non un errore.

Un insieme vuoto significa selezione di nessun codice, mai selezione di tutti.
Un profilo intenzionalmente vuoto va segnalato; una configurazione incompleta non
attiva un default nascosto. Una modifica a un preset produce un profilo derivato
con un'identità diversa, non ridefinisce in silenzio il preset pubblicato.

Per una vista di supporti con codici E, il predicato di ammissione è:

```text
accepted(E, policy) = bool(E INTERSECTION policy.allowed_codes)
```

Il predicato non elimina gli altri codici dal dettaglio sorgente.
La selezione delle evidenze non sostituisce i controlli su GO, polarità,
associazione autorizzata, contesto o eligibility finale.

### Esempio di sintassi proposta, non ancora supportata dal programma

Il percorso del registro è illustrativo. Il registro deve definire i gruppi
citati e documentarne la versione; non viene creato da questo addendum.

```yaml
schema_version: probe-evidence-policy-v1
registry:
  file: config/evidence_registry.yaml
  comparison_mode: shared_snapshot

collection:
  retain_all_observed_codes: true

unclassified:
  collection: preserve_and_report
  final_benchmark: error_until_reviewed

profiles:
  my_experimental_subset:
    include_groups:
      - experimental_traditional
      - experimental_high_throughput
    include_codes: []
    exclude_codes: [IPI]

  direct_assays_and_mutants:
    include_groups: []
    include_codes: [IDA, IMP]
    exclude_codes: []
```

I due profili sono alternative, non vengono uniti automaticamente. La scelta di
uno di essi per gli eventi è un parametro esplicito. K0, neutralità e NOT hanno
riferimenti a profili propri, da registrare quando le rispettive fasi saranno
attivate. La sintassi dell'esempio non cambia le regole oggi congelate.

## 4. Codici nuovi e codici esclusi non sono la stessa cosa

La distinzione minima è fra osservato, registrato, selezionato e non selezionato.
La fase di raccolta non elimina mai una riga per la sola novità del codice.

Prima di pubblicare il benchmark, propongo il controllo rigoroso
`error_until_reviewed` sui codici non risolti presenti nei supporti dei donor.
In particolare, un codice ignoto a START non può essere trattato tacitamente
come assenza di conoscenza pregressa: potrebbe generare falsa novità a END.

L'utente può fornire una definizione personalizzata, scegliere esplicitamente
inclusione o esclusione, oppure aggiornare il registro da fonti verificate.
La scelta viene registrata con origine e motivazione. Una modalità alternativa
che sospenda solo i target/aspetti coinvolti richiede attenzione alle dipendenze
tra aspetti per le categorie di conoscenza globale; non è un default implicito.

NAS e ND restano esclusi nei preset attuali. Nel motore generalizzato queste
esclusioni sono dati dei preset, non divieti non modificabili nel parser.
L'inclusione esplicita in un profilo personalizzato non rende un codice
sperimentale e non recupera righe già eliminate upstream dal normalizzato.

## 5. Evoluzione delle definizioni e confronto temporale

Per il confronto principale si congela un registro interpretativo e una policy
per scopo, applicati in modo coerente a START e END. Non si usa automaticamente
la classificazione antica per START e quella nuova per END.

Le informazioni storiche sulle definizioni sono conservate quando disponibili,
separatamente dalla classificazione operativa del run. Se la semantica di un
codice cambia davvero tra epoche, una tabella condivisa non ricostruisce da sola
il significato originale: occorrono regole di armonizzazione documentate oppure
uno stato esplicito di incompatibilità da risolvere prima del benchmark.

Una modifica soltanto al registro è un cambiamento di configurazione, non un
nuovo esperimento biologico. I risultati ottenuti con registri diversi vanno
confrontati come analisi di sensibilità distinte.

Nel nuovo percorso l'upgrade va espresso come ingresso nello stesso insieme
ammesso, non come diminuzione di un tier numerico fisso. Rimangono separate
acquisizione del termine, ingresso nel profilo, variazione del solo contesto e
cambiamento di interpretazione dovuto alla configurazione. Non si riusa senza
adattamento il ramo di `EvidencePolicy.is_upgrade()` basato sui tier chiusi.

## 6. Donor diversi per release: invariante confermata

```text
D_START(target) = donor YES deliberati in START
D_END(target)   = donor YES deliberati in END

A_START(target) = righe GOA START collegate tramite D_START(target)
A_END(target)   = righe GOA END collegate tramite D_END(target)
```

D_START e D_END possono coincidere oppure differire. Non si usa la loro unione
come autorizzazione globale. Condividere registro e policy non significa
condividere donor o asserzioni tra le release. La chiave Stage 07 resta
`(release, target_id, rid)` e non viene sostituita da una mappa alias-sequenza.

## 7. Propagazione senza perdita delle evidenze

La chiusura strutturale GO resta indipendente dal registro delle evidenze.
Ogni supporto conserva codice, polarità, donor, sorgente e termine diretto.
Gli antenati accumulano supporti compatibili; un codice non viene convertito in
un codice più forte né sostituito dal migliore tier.

Una vista filtrata si costruisce dai supporti ammessi per lo scopo, non eliminando
a posteriori interi GO che possiedono anche evidenze escluse. Un antenato con
supporti IDA e IEA rimane ammissibile in un profilo IDA, mantenendo IEA nel dettaglio
completo. Un percorso multiplo non duplica un supporto indipendente.

NOT resta un attributo dell'asserzione, non una categoria di evidence code.
L'ammissione di un codice non converte mai una riga NOT in verità positiva.

## 8. Riproducibilità e invalidazione

Conservare il registro effettivo, gli eventuali override, i gruppi risolti, i
codici effettivamente ammessi per scopo, la versione dello schema e del compilatore,
le identità dei file sorgente e gli SHA256 reali. Se sono importate risorse ECO,
conservarne versione, bytes o snapshot riproducibile e mapping utilizzato.

Il fingerprint semantico copre contenuto del registro e della policy compilata,
non soltanto nome o elenco dei tier. Identificare separatamente il run completo,
che comprende anche dati annotativi e associazioni.

Nessun aggiornamento web implicito durante un run. L'aggiornamento del registro
è un'azione esplicita fuori dal normale calcolo del benchmark.

Il recupero completo delle righe e il censimento raw non dipendono dalla selezione
delle evidenze. Cambiare selezione/registro ricalcola le viste interpretate e i
prodotti downstream dipendenti, non Stage 01-07 né una nuova scansione dei GAF
se il materiale estratto completo è già disponibile e valido.
Una futura modifica del parser o delle regole upstream resta un caso diverso.

### 8.1 Matrice di ricalcolo confermata

La matrice assume invariati gli altri input e validi i bundle riutilizzati.
Per modifica si intende un cambiamento delle dipendenze scientifiche o tecniche
effettive dello stage, non una semplice modifica a un documento descrittivo.

| Cambiamento | Ricalcolo richiesto | Prodotti da riutilizzare |
|---|---|---|
| Nessun input, parametro o codice rilevante cambia | Nessuno, se gli output sono validi | Tutti i bundle validi |
| Cambia una policy di Stage 07 | Stage 07, Stage 08 e gli effettivi dipendenti a valle | Stage 01-06C1 invariati |
| Stage 07 invariato; cambiano registro o selezione delle evidenze Stage 08 | Prodotti interpretati Stage 08 e dipendenti a valle | Stage 01-07 e raccolta completa Stage 08 compatibile |
| Cambia un parametro di recupero o il parser Stage 08 | Recupero Stage 08 interessato e dipendenti a valle | Stage 01-07 invariati |
| Cambia una sorgente o una policy scientifica upstream | Stage upstream coinvolto e sue dipendenze effettive | Soltanto i prodotti non dipendenti dalla modifica |

Stage 08 non deve invalidare Stage 07 attraverso un parametro che appartiene
soltanto a Stage 08. Anche se si usa un unico YAML, ciascuna regola deve dipendere
solo dalla configurazione e dai sorgenti pertinenti al proprio contratto.
Un nuovo commit globale non deve, da solo, obbligare al ricalcolo di tutti gli
stage. Un cambiamento effettivo di un modulo condiviso resta invece rilevante
per gli stage che dipendono da quel modulo.

### 8.2 Separazione fra recupero e interpretazione dentro Stage 08

Il livello di raccolta conserva tutte le righe annotate e i codici osservati;
non incorpora il filtro delle evidenze. Il livello interpretativo applica il
registro e le selezioni per scopo a quella raccolta completa.

Cambiare soltanto registro, categorie o codici selezionati deve quindi poter
ricalcolare i prodotti interpretati senza una nuova scansione dei grandi GAF,
quando la raccolta completa è disponibile, integra e compatibile. Questa
separazione è logica: i nomi di eventuali sottostage non sono ancora fissati.

Quando cambia Stage 07, Stage 08 deve essere invalidato e ricostruito rispetto
al nuovo bundle: collegamenti, copertura e provenienza non possono restare
quelli della precedente selezione dei donor. Un'eventuale cache di righe sorgente
è riutilizzabile soltanto se copre tutti i donor ora richiesti nella corretta
release, con lo stesso GAF e lo stesso contratto di parsing. La copertura deve
includere anche gli accession cercati senza risultati. L'assenza di righe in una
cache non dimostra da sola che il donor sia stato cercato.

Se la nuova selezione introduce donor non coperti, occorre recuperarne le righe;
non si può dichiarare completa la nuova estrazione usando il vecchio sottoinsieme.
Il riuso della cache è un'ottimizzazione verificabile, non una deroga alla
ricostruzione della provenienza Stage 07 -> Stage 08.

### 8.3 Invalidazione controllata e pubblicazione

Mantenere un'unica destinazione canonica Stage 08 nel run, senza richiedere
una directory permanente per ogni combinazione di parametri. Le eventuali copie
storiche devono essere archiviate esplicitamente, come previsto dal contratto
Stage 07 -> Stage 08.

La sostituzione passa dal contratto di invalidazione del workflow. Non cancellare
manualmente manifest o bundle validi per forzare l'esecuzione. I nuovi prodotti
sono costruiti e validati in staging e pubblicati atomicamente. Un'interruzione
non deve lasciare un bundle misto, né far accettare un vecchio risultato come
valido per la nuova configurazione. Manifest e parametri devono identificare
il bundle Stage 07 realmente consumato e le policy Stage 08 realmente applicate.

### 8.4 Collocazione della documentazione

Salvare questo addendum nel repository sul server:

```text
/data/PROGENY/ProBE-chatgpt/docs/STAGE08_EVIDENCE_POLICY_GENERALIZZATA.md
```

Mantenerlo accanto a `STAGE08_AUDIT_E_CONTRATTO_PROPOSTO.md`, che descrive l'audit
e il primo incremento. Non sostituisce il contratto congelato
`STAGE07_STAGE08_INTERFACE_CONTRACT.md` e non va collocato nei bundle del run.

Il salvataggio e l'eventuale commit sono operazioni di documentazione: non
implementano Stage 08, non eseguono la pipeline e non devono, da soli,
invalidare i prodotti scientifici. Registri e parametri realmente utilizzati
sono invece input da tracciare, distinti da questo testo descrittivo.

## 9. Integrazione incrementale

Primo incremento: raccolta e attribuzione delle righe, inventario dinamico dei
codici, stato separato per donor senza annotazioni; nessun filtro di evidenza.

Incremento seguente: loader del registro e compilatore di selezione generico,
con confronto di compatibilità rispetto ai preset attuali. Registri e profili
non richiedono nuove dipendenze o un caricamento ECO obbligatorio.

Solo successivamente: adattamento esplicito di confronto temporale, K0,
neutralità, selezione finale e trattamento NOT. Non introdurre global monkey
patch delle costanti del modulo esistente o un secondo sistema di ranking nascosto.

## 10. Test sintetici richiesti, non ancora eseguiti

1. Un codice nuovo viene conservato e censito senza modificare il parser.
2. L'utente aggiunge quel codice al registro e lo seleziona senza modificare Python.
3. Gruppi sovrapposti producono un insieme deduplicato deterministico.
4. Esclusione esplicita prevale; insieme vuoto non diventa selezione universale.
5. Un gruppo inesistente o un codice configurato non registrato produce errore.
6. Cambiare ordine di codici/gruppi non cambia la policy compilata semanticamente.
7. Codici ignoti a START non generano automaticamente nuova conoscenza END.
8. Cambiare soltanto il registro non viene registrato come acquisizione biologica.
9. K0 e benchmark possono selezionare evidenze diverse senza contaminarsi.
10. Codici esclusi conservano righe, polarità e riferimenti nell'archivio completo.
11. Donor autorizzato soltanto in END non contribuisce a START.
12. Un antenato conserva un supporto ammesso anche se altri supporti sono esclusi.
13. Le due polarità rimangono separate per qualsiasi configurazione dei codici.
14. I preset di compatibilità mantengono i comportamenti documentati, incluse le
    esclusioni NAS/ND e la differenza experimental_strict/experimental_all.
15. Con input, policy e implementazione invariati, il workflow non pianifica
    ricalcoli di Stage 07 o Stage 08 già validi.
16. Cambiare soltanto una policy Stage 07 pianifica 07, 08 e i dipendenti,
    senza ricalcolare Stage 01-06C1.
17. Cambiare soltanto selezione/registro Stage 08 non pianifica Stage 01-07
    e non rilegge il GAF quando la raccolta completa compatibile è disponibile.
18. Un donor aggiunto da Stage 07 ma non coperto dalla raccolta precedente
    richiede recupero esplicito: non viene interpretato come privo di GO.
19. Un'interruzione durante il ricalcolo non pubblica output parziali e non
    rende valido il vecchio bundle per la nuova configurazione.
20. La sola modifica di documentazione o di parametri estranei allo stage
    non invalida quel bundle; i parametri pertinenti restano invece tracciati.

## 11. Fonti e limiti della verifica

Fonti di progetto: `stage08_logica_annotazioni_sdeRCZ.txt`, sezioni
`records.py`, `parsing/gaf.py`, `evidence.py`, `truth.py`; contratto Stage 07 -> 08
nel resoconto `stage08_ricognizione_20261009_174848.txt`; audit precedente
`STAGE08_AUDIT_E_CONTRATTO_PROPOSTO.md`.

Fonti esterne consultate il 10 ottobre 2026:

- GO, GAF 2.2: `https://geneontology.org/docs/go-annotation-file-gaf-format-2.2/`
- GO, guida evidence code: `https://www.geneontology.org/docs/guide-go-evidence-codes/`
- ECO, mapping pubblicato:
  `https://github.com/evidenceontology/evidenceontology/blob/master/gaf-eco-mapping.txt`

Il mapping ECO consultato dichiara che da gennaio 2026 i mapping specifici GO_REF
sono stati spostati nella gestione GO e che il file contiene i mapping di default.
Questa osservazione dimostra l'importanza di versionare anche gli adattatori delle
fonti. Non è stata verificata qui la struttura della risorsa di destinazione,
non sono stati importati snapshot e non è stato compilato un registro production.

La classificazione dei codici effettivamente presenti nei donor del run canonico
non è stata ancora censita: il documento definisce come eseguire e usare quel
censimento senza confonderlo con la semantica ufficiale dei codici.
