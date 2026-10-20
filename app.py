import calendar
import copy
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st
from calendario import (
    CODICI_ASSENZA,
    controlla_calendario,
    crediti_assenza_minuti,
    festivita_nazionali,
    genera_alternative,
    genera_calendario,
    is_festivo,
    ore_calendario_per_persona,
    riepilogo_calendario,
)
from excel_report import crea_report_excel

# Cartella dove vengono salvati i dati: una struttura = un file .json
CARTELLA_DATI = Path(__file__).parent / "dati"
CARTELLA_DATI.mkdir(exist_ok=True)

COLONNE = [
    "Nome", "Categoria", "Almeno 1 festivo", "Mattina", "Pomeriggio", "Notte",
    "Ore settimanali",
]
COLONNE_TURNI = ["Mattina", "Pomeriggio", "Notte"]
CATEGORIE = ["Di ruolo", "Solo festivi"]
FORMATO_ORA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

TURNI = ["Mattina", "Pomeriggio", "Notte"]
TIPI_ASSENZA = ["Ferie", "Riposo", "Malattia", "Congedo straordinario (CS)"]
MESI = [
    "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
    "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre",
]
GIORNI = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]

COLONNE_QUOTE = ["Nome", "M min", "M max", "P min", "P max", "N min", "N max"]
COLONNE_ASSENZE = ["Nome", "Tipo", "Dal", "Al", "Turno previsto"]
COLONNE_ASSENZE_RICHIESTE = ["Nome", "Tipo", "Dal", "Al"]
COLONNE_PREDETERMINATI = ["Nome", "Data", "Turno"]
COLONNE_FESTIVI_EXTRA = ["Data", "Descrizione"]

# Regole di partenza per ogni nuova struttura (modificabili dalla scheda "Regole")
REGOLE_DEFAULT = {
    "turni": [
        {"Turno": "Mattina", "Inizio": "08:00", "Fine": "14:20"},
        {"Turno": "Pomeriggio", "Inizio": "14:00", "Fine": "20:20"},
        {"Turno": "Notte", "Inizio": "20:00", "Fine": "08:00"},
    ],
    "copertura": [
        {"Turno": "Mattina", "Feriale": 6, "Festivo": 1},
        {"Turno": "Pomeriggio", "Feriale": 2, "Festivo": 1},
        {"Turno": "Notte", "Feriale": 1, "Festivo": 1},
    ],
    "pomeriggio_feriale_minimo": 1,
    "riposo_minimo_ore": 11,
    "scostamento_max_turni": 2,   # al massimo +2 turni rispetto al monte ore mensile
    "max_turni_consecutivi": 6,   # ogni 6 turni di lavoro serve 1 giorno di riposo
}


# ---------- Lettura e scrittura dei dati ----------
def percorso(struttura: str) -> Path:
    return CARTELLA_DATI / f"{struttura}.json"


def elenco_strutture() -> list[str]:
    return sorted(p.stem for p in CARTELLA_DATI.glob("*.json"))


def carica_dati(struttura: str) -> dict:
    file = percorso(struttura)
    dati = json.loads(file.read_text(encoding="utf-8")) if file.exists() else {}
    dati.setdefault("personale", [])
    dati.setdefault("mesi", {})
    regole = dati.setdefault("regole", {})
    for chiave, valore in REGOLE_DEFAULT.items():
        regole.setdefault(chiave, copy.deepcopy(valore))
    return dati


def salva_dati(struttura: str, dati: dict) -> None:
    dati["struttura"] = struttura
    percorso(struttura).write_text(
        json.dumps(dati, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def invalida_calendari(dati: dict) -> None:
    """Rimuove i calendari generati con dati di personale o regole ormai superati."""
    for pianificazione in dati["mesi"].values():
        if not pianificazione.get("approvazione"):
            pianificazione.pop("calendario", None)


def storico_minuti_approvati(dati: dict, anno: int, mese: int) -> dict[str, int]:
    """Ore validate nei mesi precedenti dello stesso anno, in minuti."""
    storico: dict[str, int] = {}
    for chiave, pianificazione in dati["mesi"].items():
        try:
            anno_mese, mese_numero = map(int, chiave.split("-"))
        except ValueError:
            continue
        approvazione = pianificazione.get("approvazione")
        if anno_mese != anno or mese_numero >= mese or not approvazione:
            continue
        for nome, minuti in approvazione.get("minuti_credito", {}).items():
            storico[nome] = storico.get(nome, 0) + int(minuti)
    return storico


def storico_mesi_approvati(dati: dict, anno: int, mese: int) -> dict[int, dict[str, int]]:
    storico: dict[int, dict[str, int]] = {}
    for chiave, pianificazione in dati["mesi"].items():
        try:
            anno_mese, mese_numero = map(int, chiave.split("-"))
        except ValueError:
            continue
        approvazione = pianificazione.get("approvazione")
        if anno_mese == anno and mese_numero < mese and approvazione:
            storico[mese_numero] = {
                nome: int(minuti)
                for nome, minuti in approvazione.get("minuti_credito", {}).items()
            }
    return storico


def giorni_storico_approvati(dati: dict, anno: int, mese: int) -> int:
    """Giorni dei mesi già validati nell'anno: servono per l'obiettivo ore progressivo."""
    return sum(
        calendar.monthrange(anno, numero)[1]
        for numero in storico_mesi_approvati(dati, anno, mese)
    )


def calendario_mese_precedente(dati: dict, anno: int, mese: int) -> list[dict]:
    """Turni del mese precedente (validati, se ci sono): garantiscono la continuità
    di smonto, riposo e giorni consecutivi a cavallo tra due mesi."""
    anno_prec, mese_prec = (anno - 1, 12) if mese == 1 else (anno, mese - 1)
    pianificazione = dati["mesi"].get(f"{anno_prec}-{mese_prec:02d}", {})
    approvazione = pianificazione.get("approvazione")
    if approvazione:
        return approvazione.get("calendario", [])
    return pianificazione.get("calendario", [])


# ---------- Funzioni di supporto ----------
def normalizza(df: pd.DataFrame) -> pd.DataFrame:
    """Rende la tabella del personale ordinata e con i tipi di dato corretti."""
    df = df.reindex(columns=COLONNE).copy()
    df["Nome"] = df["Nome"].fillna("").astype(str)
    df["Categoria"] = df["Categoria"].where(df["Categoria"].isin(CATEGORIE), "Di ruolo")
    df["Almeno 1 festivo"] = df["Almeno 1 festivo"].fillna(True).astype(bool)
    for colonna in COLONNE_TURNI:
        df[colonna] = df[colonna].fillna(False).astype(bool)
    df["Ore settimanali"] = pd.to_numeric(df["Ore settimanali"], errors="coerce").fillna(38.0)
    return df.reset_index(drop=True)


def minuti(ora: str) -> int:
    ore, minuti_ = ora.split(":")
    return int(ore) * 60 + int(minuti_)


def durata_minuti(inizio: str, fine: str) -> int:
    """Durata di un turno in minuti; gestisce i turni che passano la mezzanotte."""
    durata = (minuti(fine) - minuti(inizio)) % (24 * 60)
    return durata if durata else 24 * 60


def formatta(durata: int) -> str:
    return f"{durata // 60}h{durata % 60:02d}"


def tabella_quote(nomi: list[str], salvate: list[dict]) -> pd.DataFrame:
    """Una riga per ogni persona; recupera i valori già salvati per il mese."""
    precedenti = {riga["Nome"]: riga for riga in salvate}
    righe = []
    for nome in nomi:
        riga = {colonna: None for colonna in COLONNE_QUOTE}
        riga.update(precedenti.get(nome, {}))
        riga["Nome"] = nome
        righe.append(riga)
    df = pd.DataFrame(righe, columns=COLONNE_QUOTE)
    for colonna in COLONNE_QUOTE[1:]:
        df[colonna] = pd.to_numeric(df[colonna], errors="coerce")
    return df


def tabella_con_date(salvate: list[dict], colonne: list[str], colonne_data: list[str]) -> pd.DataFrame:
    df = pd.DataFrame(salvate, columns=colonne)
    for colonna in colonne_data:
        df[colonna] = pd.to_datetime(df[colonna], errors="coerce").dt.date
    return df


def tabella_assenze(salvate: list[dict]) -> pd.DataFrame:
    righe = copy.deepcopy(salvate)
    for riga in righe:
        if riga.get("Tipo") == "Permesso":
            riga["Tipo"] = "Congedo straordinario (CS)"
    df = tabella_con_date(righe, COLONNE_ASSENZE, ["Dal", "Al"])
    df["Turno previsto"] = df["Turno previsto"].fillna("").astype(str)
    return df


def righe_da_tabella(df: pd.DataFrame, colonne_data=(), colonne_intere=()) -> list[dict]:
    """Trasforma una tabella in una lista di dizionari pronta per il file JSON."""
    righe = []
    for riga in df.to_dict(orient="records"):
        pulita = {}
        for chiave, valore in riga.items():
            if pd.isna(valore):
                pulita[chiave] = None
            elif chiave in colonne_data:
                pulita[chiave] = pd.Timestamp(valore).date().isoformat()
            elif chiave in colonne_intere:
                pulita[chiave] = int(valore)
            elif hasattr(valore, "item"):
                pulita[chiave] = valore.item()
            else:
                pulita[chiave] = valore
        righe.append(pulita)
    return righe


def festivi_del_mese(anno: int, mese: int, extra: dict[date, str]) -> pd.DataFrame:
    """Domeniche, festività nazionali ed eventuali festività locali del mese."""
    nazionali = festivita_nazionali(anno)
    righe = []
    for giorno in range(1, calendar.monthrange(anno, mese)[1] + 1):
        data = date(anno, mese, giorno)
        motivo = nazionali.get(data) or extra.get(data)
        if not motivo and data.weekday() == 6:
            motivo = "Domenica"
        if motivo:
            righe.append(
                {"Data": data.strftime("%d/%m/%Y"), "Giorno": GIORNI[data.weekday()], "Motivo": motivo}
            )
    return pd.DataFrame(righe, columns=["Data", "Giorno", "Motivo"])

COLORI_CODICI = {
    "M": "#D9EAF7", "P": "#E2F0D9", "N": "#B4C6E7", "S": "#FFF2CC", "R": "#F2F2F2",
    "F": "#FCE4D6", "Mal": "#F4CCCC", "CS": "#D9D2E9",
}


def griglia_calendario(
    nomi: list[str],
    anno: int,
    mese: int,
    pianificazione: dict,
    assegnazioni: list[dict],
    precedente: list[dict],
) -> pd.DataFrame:
    """Tabella persone x giorni con i codici M, P, N, S (smonto), R, F, Mal, CS."""
    giorni = [date(anno, mese, n) for n in range(1, calendar.monthrange(anno, mese)[1] + 1)]
    codici_turno = {"Mattina": "M", "Pomeriggio": "P", "Notte": "N"}
    lavoro = {}
    for riga in assegnazioni:
        giorno = date.fromisoformat(str(riga["Data"])[:10])
        lavoro[(str(riga["Nome"]).strip(), giorno)] = codici_turno.get(riga["Turno"], "")
    giorno_prima = (giorni[0] - timedelta(days=1)).isoformat()
    notti_prima = {
        str(riga.get("Nome", "")).strip()
        for riga in precedente
        if riga.get("Turno") == "Notte" and str(riga.get("Data"))[:10] == giorno_prima
    }
    assenze = {}
    for assenza in pianificazione.get("assenze", []):
        if not assenza.get("Dal") or not assenza.get("Al"):
            continue
        codice = CODICI_ASSENZA.get(str(assenza.get("Tipo") or ""), "CS")
        codice = "Mal" if codice == "M" else codice
        dal = date.fromisoformat(str(assenza["Dal"])[:10])
        al = date.fromisoformat(str(assenza["Al"])[:10])
        for giorno in giorni:
            if dal <= giorno <= al:
                assenze[(str(assenza.get("Nome", "")).strip(), giorno)] = codice
    righe = []
    for nome in nomi:
        riga = {"Nome": nome}
        for giorno in giorni:
            if (nome, giorno) in assenze:
                codice = assenze[(nome, giorno)]
            elif (nome, giorno) in lavoro:
                codice = lavoro[(nome, giorno)]
            else:
                dopo_notte = (
                    lavoro.get((nome, giorno - timedelta(days=1))) == "N"
                    or (giorno == giorni[0] and nome in notti_prima)
                )
                codice = "S" if dopo_notte else "R"
            riga[f"{giorno.day:02d}{'LMMGVSD'[giorno.weekday()]}"] = codice
        righe.append(riga)
    return pd.DataFrame(righe).set_index("Nome")


def colora_codice(valore: str) -> str:
    colore = COLORI_CODICI.get(valore)
    return f"background-color: {colore}; color: #000000;" if colore else ""


def mostra_alternative(
    scelta: str,
    dati: dict,
    nomi: list[str],
    anno: int,
    mese: int,
    chiave: str,
    suffisso: str,
    versione: int,
    validato: bool,
) -> None:
    """Confronto delle proposte generate e scelta di quella preferita."""
    contenitore = st.session_state.get("alternative")
    if (
        validato
        or chiave not in dati["mesi"]
        or not contenitore
        or contenitore.get("struttura") != scelta
        or contenitore.get("mese") != chiave
    ):
        return
    proposte = contenitore["proposte"]
    st.markdown("**Proposte di calendario**")
    if not proposte:
        st.info("Non ho trovato proposte valide: controlla i vincoli del mese.")
        return
    if len(proposte) == 1:
        st.info(
            "Ho trovato una sola proposta: con i vincoli di questo mese non esistono "
            "altri calendari abbastanza diversi."
        )
    st.caption(
        "Tutte le proposte rispettano le regole obbligatorie. Confronta gli indicatori, "
        "guarda le griglie e premi Scegli sulla proposta preferita: diventerà il calendario "
        "del mese."
    )
    pianificazione = dati["mesi"][chiave]
    precedente = calendario_mese_precedente(dati, anno, mese)
    riepiloghi = [
        riepilogo_calendario(
            dati["personale"], dati["regole"], anno, mese, pianificazione, proposta, precedente
        )
        for proposta in proposte
    ]
    prima = {(r["Data"], r["Turno"], r["Nome"]) for r in proposte[0]}
    confronto = []
    for indice, (proposta, (indicatori, _)) in enumerate(zip(proposte, riepiloghi), start=1):
        attuale = {(r["Data"], r["Turno"], r["Nome"]) for r in proposta}
        confronto.append(
            {
                "Proposta": indice,
                "Pomeriggi ridotti (giorni)": indicatori.get("pomeriggi_ridotti"),
                "Mattine ridotte (giorni)": indicatori.get("mattine_ridotte"),
                "Notte, smonto, notte": indicatori.get("notte_smonto_notte"),
                "Persone fuori dal limite ore": indicatori.get("persone_fuori_limite"),
                "Scostamento massimo (turni)": indicatori.get("scostamento_massimo_turni"),
                "Turni diversi dalla proposta 1": len(attuale - prima),
            }
        )
    st.dataframe(pd.DataFrame(confronto), hide_index=True)

    schede = st.tabs([f"Proposta {indice}" for indice in range(1, len(proposte) + 1)])
    for indice, (scheda, proposta, (_, righe)) in enumerate(
        zip(schede, proposte, riepiloghi), start=1
    ):
        with scheda:
            griglia = griglia_calendario(nomi, anno, mese, pianificazione, proposta, precedente)
            st.dataframe(griglia.style.map(colora_codice))
            st.caption("M mattina · P pomeriggio · N notte · S smonto · R riposo · F ferie · Mal malattia · CS congedo")
            st.dataframe(pd.DataFrame(righe), hide_index=True)
            if st.button(f"Scegli la proposta {indice}", type="primary", key=f"scegli_{suffisso}_{indice}"):
                violazioni, avvisi = controlla_calendario(
                    dati["personale"], dati["regole"], anno, mese, pianificazione, proposta, precedente
                )
                if violazioni:
                    for violazione in violazioni:
                        st.error(violazione)
                else:
                    pianificazione["calendario"] = proposta
                    salva_dati(scelta, dati)
                    st.session_state.pop("alternative", None)
                    st.session_state["avvisi"] = avvisi
                    st.session_state["versione_mese"] = versione + 1
                    st.session_state["messaggio"] = (
                        f"Proposta {indice} scelta come calendario di {MESI[mese - 1]} {anno}. "
                        "Controllalo e imposta Validato su Sì per archiviarlo."
                    )
                    st.rerun()


def mostra_pianificazione(scelta: str, dati: dict, nomi: list[str]) -> None:
    """Scheda con quote mensili, assenze, turni predeterminati e festività."""
    if not nomi:
        st.info("Inserisci prima il personale nella scheda Personale.")
        return

    oggi = date.today()
    mese_pred = oggi.month % 12 + 1
    anno_pred = oggi.year + (1 if oggi.month == 12 else 0)

    colonna_mese, colonna_anno, _ = st.columns([1, 1, 3])
    mese = colonna_mese.selectbox(
        "Mese", list(range(1, 13)), index=mese_pred - 1,
        format_func=lambda m: MESI[m - 1], key="mese_sel",
    )
    anno = int(colonna_anno.number_input("Anno", min_value=2024, max_value=2100, value=anno_pred, step=1, key="anno_sel"))

    chiave = f"{anno}-{mese:02d}"
    salvato = dati["mesi"].get(chiave, {})
    approvazione = salvato.get("approvazione")
    validato = bool(approvazione)
    primo_giorno = date(anno, mese, 1)
    ultimo_giorno = date(anno, mese, calendar.monthrange(anno, mese)[1])
    versione = st.session_state.get("versione_mese", 0)
    suffisso = f"{scelta}_{chiave}_{versione}"

    st.markdown("**Turni del mese per ciascuna persona**")
    st.caption(
        "Per ogni tipo di turno indica il minimo e il massimo che la persona può fare in questo mese. "
        "Minimo vuoto significa 0; massimo vuoto significa nessun limite. "
        "Le quote si applicano a tutto il personale, compreso chi è disponibile solo nei festivi. "
        "Per aggiungere o togliere persone usa la scheda Personale."
    )
    quote_mod = st.data_editor(
        tabella_quote(nomi, salvato.get("quote", [])),
        hide_index=True,
        disabled=True if validato else ["Nome"],
        key=f"quote_{suffisso}",
        column_config={
            colonna: st.column_config.NumberColumn(
                titolo, min_value=0, max_value=31, step=1, format="%d"
            )
            for colonna, titolo in [
                ("M min", "Mattine min"), ("M max", "Mattine max"),
                ("P min", "Pomeriggi min"), ("P max", "Pomeriggi max"),
                ("N min", "Notti min"), ("N max", "Notti max"),
            ]
        },
    )

    st.markdown("**Assenze**")
    st.caption(
        "Tipi: Ferie (F), Riposo (R), Malattia (M), Congedo straordinario (CS). "
        "Ferie, malattia e congedo richiedono il turno previsto per calcolare il credito ore; "
        "il riposo non accredita ore."
    )
    assenze_mod = st.data_editor(
        tabella_assenze(salvato.get("assenze", [])),
        num_rows="dynamic",
        hide_index=True,
        disabled=validato,
        key=f"assenze_{suffisso}",
        column_config={
            "Nome": st.column_config.SelectboxColumn("Nome", options=nomi, required=True),
            "Tipo": st.column_config.SelectboxColumn(
                "Tipo", options=TIPI_ASSENZA, default="Ferie", required=True
            ),
            "Dal": st.column_config.DateColumn("Dal", format="DD/MM/YYYY", required=True),
            "Al": st.column_config.DateColumn("Al", format="DD/MM/YYYY", required=True),
            "Turno previsto": st.column_config.SelectboxColumn(
                "Turno previsto (credito ore)", options=["", *TURNI], default=""
            ),
        },
    )

    st.markdown("**Turni predeterminati**")
    st.caption("Turni già decisi: il programma li terrà fissi e organizzerà gli altri intorno.")
    predeterminati_mod = st.data_editor(
        tabella_con_date(salvato.get("predeterminati", []), COLONNE_PREDETERMINATI, ["Data"]),
        num_rows="dynamic",
        hide_index=True,
        disabled=validato,
        key=f"predeterminati_{suffisso}",
        column_config={
            "Nome": st.column_config.SelectboxColumn("Nome", options=nomi, required=True),
            "Data": st.column_config.DateColumn(
                "Data", format="DD/MM/YYYY", min_value=primo_giorno, max_value=ultimo_giorno, required=True
            ),
            "Turno": st.column_config.SelectboxColumn("Turno", options=TURNI, required=True),
        },
    )

    st.markdown("**Festività locali del mese**")
    st.caption(
        "Le domeniche e le festività nazionali sono già comprese. "
        "Aggiungi qui eventuali altri giorni festivi, come il patrono."
    )
    extra_mod = st.data_editor(
        tabella_con_date(salvato.get("festivi_extra", []), COLONNE_FESTIVI_EXTRA, ["Data"]),
        num_rows="dynamic",
        hide_index=True,
        disabled=validato,
        key=f"extra_{suffisso}",
        column_config={
            "Data": st.column_config.DateColumn(
                "Data", format="DD/MM/YYYY", min_value=primo_giorno, max_value=ultimo_giorno, required=True
            ),
            "Descrizione": st.column_config.TextColumn("Descrizione"),
        },
    )

    extra = {}
    for riga in extra_mod.dropna(subset=["Data"]).itertuples():
        descrizione = str(riga.Descrizione) if pd.notna(riga.Descrizione) else "Festività locale"
        extra[pd.Timestamp(riga.Data).date()] = descrizione

    st.markdown(f"**Giorni festivi di {MESI[mese - 1]} {anno}**")
    st.caption("Conta come festiva anche la notte che precede un giorno festivo (per esempio il sabato notte).")
    st.dataframe(festivi_del_mese(anno, mese, extra), hide_index=True)

    colonna_salva, colonna_genera, colonna_alternative = st.columns(3)
    salva_mese = colonna_salva.button("Salva pianificazione del mese", disabled=validato)
    genera_mese = colonna_genera.button(
        "Genera calendario Excel", type="primary", disabled=validato
    )
    genera_alt = colonna_alternative.button(
        "Genera 3 proposte da confrontare",
        disabled=validato,
        help="Calcola fino a 3 calendari validi e diversi tra loro (circa 1-2 minuti).",
    )

    if salva_mese or genera_mese or genera_alt:
        errori = []

        for lettera, nome_turno in (("M", "mattine"), ("P", "pomeriggi"), ("N", "notti")):
            minimo, massimo = quote_mod[f"{lettera} min"], quote_mod[f"{lettera} max"]
            sbagliate = quote_mod[minimo.notna() & massimo.notna() & (minimo > massimo)]
            if not sbagliate.empty:
                errori.append(
                    f"Per le {nome_turno} il minimo supera il massimo per: {', '.join(sbagliate['Nome'])}."
                )

        assenze = assenze_mod.dropna(subset=COLONNE_ASSENZE_RICHIESTE)
        if (pd.to_datetime(assenze["Al"]) < pd.to_datetime(assenze["Dal"])).any():
            errori.append("In una assenza la data finale è precedente a quella iniziale.")
        for _, riga in assenze.iterrows():
            tipo = str(riga["Tipo"])
            codice = CODICI_ASSENZA.get(tipo, "")
            turno_previsto = riga.get("Turno previsto")
            if tipo == "Permesso":
                tipo = "Congedo straordinario (CS)"
                codice = "CS"
            if codice != "R" and (
                not isinstance(turno_previsto, str)
                or turno_previsto not in TURNI
            ):
                errori.append(
                    f"Seleziona il turno previsto per {riga['Nome']} ({tipo}) "
                    "per calcolare il credito ore."
                )

        predeterminati = predeterminati_mod.dropna(subset=COLONNE_PREDETERMINATI)
        predeterminati_incompleti = predeterminati_mod[
            predeterminati_mod[COLONNE_PREDETERMINATI].isna().any(axis=1)
        ]
        if predeterminati_incompleti.shape[0] and (genera_mese or genera_alt):
            errori.append(
                f"Ci sono {len(predeterminati_incompleti)} turni predeterminati incompleti: "
                "completa Nome, Data e Turno prima di generare il calendario."
            )
        if predeterminati.duplicated().any():
            errori.append("Ci sono due turni predeterminati identici: elimina i doppioni.")

        categorie = {
            str(persona.get("Nome", "")).strip(): persona.get("Categoria")
            for persona in dati["personale"]
        }
        turni_non_festivi = []
        for riga in predeterminati.itertuples():
            if categorie.get(str(riga.Nome).strip()) != "Solo festivi":
                continue
            data_turno = pd.Timestamp(riga.Data).date()
            festivo = is_festivo(data_turno, extra)
            if riga.Turno == "Notte":
                festivo = festivo or is_festivo(data_turno + timedelta(days=1), extra)
            if not festivo:
                turni_non_festivi.append(f"{riga.Nome} ({data_turno.strftime('%d/%m/%Y')})")
        if turni_non_festivi:
            errori.append(
                "Chi è disponibile solo nei festivi non può avere turni predeterminati "
                "nei giorni feriali: " + ", ".join(turni_non_festivi) + "."
            )
        assenze_salvate = righe_da_tabella(
            assenze, colonne_data=["Dal", "Al"]
        )
        _, errori_credito = crediti_assenza_minuti(
            {"assenze": assenze_salvate}, dati["regole"], anno, mese
        )
        errori.extend(errori_credito)

        extra_valide = extra_mod.dropna(subset=["Data"])
        scartate = (
            (len(assenze_mod) - len(assenze))
            + (len(extra_mod) - len(extra_valide))
        )

        if errori:
            for errore in errori:
                st.error(errore)
        else:
            pianificazione = {
                "quote": righe_da_tabella(quote_mod, colonne_intere=COLONNE_QUOTE[1:]),
                "assenze": assenze_salvate,
                "predeterminati": righe_da_tabella(
                    predeterminati_mod if salva_mese else predeterminati,
                    colonne_data=["Data"],
                ),
                "festivi_extra": righe_da_tabella(extra_valide, colonne_data=["Data"]),
            }
            dati["mesi"][chiave] = pianificazione
            salvato = pianificazione
            salva_dati(scelta, dati)

            if genera_alt:
                storico = storico_minuti_approvati(dati, anno, mese)
                precedente = calendario_mese_precedente(dati, anno, mese)
                with st.spinner("Calcolo delle proposte in corso: può richiedere un paio di minuti..."):
                    proposte, errori_calendario = genera_alternative(
                        dati["personale"], dati["regole"], anno, mese, pianificazione,
                        storico_minuti=storico,
                        giorni_storico=giorni_storico_approvati(dati, anno, mese),
                        calendario_precedente=precedente,
                        numero=3,
                        secondi_per_alternativa=30,
                    )
                if errori_calendario:
                    for errore in errori_calendario:
                        st.error(errore)
                    st.info("La pianificazione è stata salvata; correggi i vincoli e genera di nuovo.")
                else:
                    valide = [
                        proposta
                        for proposta in proposte
                        if not controlla_calendario(
                            dati["personale"], dati["regole"], anno, mese, pianificazione,
                            proposta, precedente,
                        )[0]
                    ]
                    st.session_state["alternative"] = {
                        "struttura": scelta, "mese": chiave, "proposte": valide,
                    }
                    st.session_state["versione_mese"] = versione + 1
                    st.session_state["messaggio"] = (
                        f"Generate {len(valide)} proposte per {MESI[mese - 1]} {anno}: "
                        "confrontale qui sotto e scegli quella preferita."
                    )
                    st.rerun()
            elif genera_mese:
                storico = storico_minuti_approvati(dati, anno, mese)
                precedente = calendario_mese_precedente(dati, anno, mese)
                assegnazioni, errori_calendario = genera_calendario(
                    dati["personale"], dati["regole"], anno, mese, pianificazione,
                    storico_minuti=storico,
                    giorni_storico=giorni_storico_approvati(dati, anno, mese),
                    calendario_precedente=precedente,
                )
                violazioni, avvisi = ([], [])
                if not errori_calendario:
                    violazioni, avvisi = controlla_calendario(
                        dati["personale"], dati["regole"], anno, mese, pianificazione,
                        assegnazioni, precedente,
                    )
                if errori_calendario:
                    for errore in errori_calendario:
                        st.error(errore)
                    st.info("La pianificazione è stata salvata; correggi i vincoli e genera di nuovo.")
                elif violazioni:
                    # Non dovrebbe accadere: il calendario non viene salvato
                    for violazione in violazioni:
                        st.error(violazione)
                    st.info("Il calendario generato non rispetta le regole e non è stato salvato.")
                else:
                    pianificazione["calendario"] = assegnazioni
                    salva_dati(scelta, dati)
                    st.session_state.pop("alternative", None)
                    st.session_state["avvisi"] = avvisi
                    st.session_state["versione_mese"] = versione + 1
                    st.session_state["messaggio"] = (
                        f"Calendario di {MESI[mese - 1]} {anno} generato. "
                        "Controllalo e imposta Validato su Sì per archiviarlo."
                    )
                    st.rerun()
            else:
                st.session_state.pop("alternative", None)
                st.session_state["versione_mese"] = versione + 1
                messaggio = f"Pianificazione di {MESI[mese - 1]} {anno} salvata."
                if len(predeterminati_incompleti):
                    messaggio += (
                        f" {len(predeterminati_incompleti)} turni predeterminati incompleti "
                        "sono stati conservati; completa Nome, Data e Turno prima di generare."
                    )
                if scartate:
                    messaggio += f" {scartate} righe incomplete sono state scartate."
                st.session_state["messaggio"] = messaggio
                st.rerun()

    mostra_alternative(scelta, dati, nomi, anno, mese, chiave, suffisso, versione, validato)

    st.markdown("**Approvazione e archivio**")
    validato_selezionato = st.checkbox(
        "Validato (Sì/No)",
        value=validato,
        key=f"validato_{suffisso}",
    )
    if validato:
        st.success(
            f"Mese archiviato il {approvazione.get('validato_il', 'data non disponibile')}. "
            "Togli la spunta e conferma per riaprire anche i mesi successivi già validati."
        )
    conferma_validazione = st.button(
        "Riapri mese e successivi"
        if validato and not validato_selezionato
        else "Conferma validazione",
        disabled=validato == validato_selezionato,
        key=f"conferma_validazione_{suffisso}",
    )

    if conferma_validazione:
        if validato and not validato_selezionato:
            for chiave_mese, pianificazione_mese in dati["mesi"].items():
                try:
                    anno_mese, mese_numero = map(int, chiave_mese.split("-"))
                except ValueError:
                    continue
                if anno_mese == anno and mese_numero >= mese:
                    pianificazione_mese.pop("approvazione", None)
                    if mese_numero > mese:
                        pianificazione_mese.pop("calendario", None)
            salva_dati(scelta, dati)
            st.session_state["versione_mese"] = versione + 1
            st.session_state["messaggio"] = (
                f"{MESI[mese - 1]} {anno} e i mesi successivi sono stati riaperti."
            )
            st.rerun()

        calendario_salvato = salvato.get("calendario")
        if not calendario_salvato:
            st.error("Genera prima il calendario del mese; poi potrai approvarlo.")
        else:
            lavorati, assenze_creditate, errori_ore = ore_calendario_per_persona(
                dati["personale"], dati["regole"], salvato, calendario_salvato, anno, mese
            )
            if errori_ore:
                for errore in errori_ore:
                    st.error(errore)
            else:
                minuti_credito = {
                    nome: lavorati.get(nome, 0) + assenze_creditate.get(nome, 0)
                    for nome in set(lavorati) | set(assenze_creditate)
                }
                salvato["approvazione"] = {
                    "validato_il": datetime.now().isoformat(timespec="seconds"),
                    "calendario": copy.deepcopy(calendario_salvato),
                    "personale": copy.deepcopy(dati["personale"]),
                    "regole": copy.deepcopy(dati["regole"]),
                    "minuti_lavorati": lavorati,
                    "minuti_assenza": assenze_creditate,
                    "minuti_credito": minuti_credito,
                }
                salva_dati(scelta, dati)
                st.session_state["versione_mese"] = versione + 1
                st.session_state["messaggio"] = (
                    f"Pianificazione di {MESI[mese - 1]} {anno} validata e archiviata."
                )
                st.rerun()

    st.caption(
        "Il file Excel scaricato è modificabile e condivisibile; le modifiche fatte in Excel "
        "non vengono reimportate automaticamente nell'app."
    )
    if salvato.get("calendario"):
        try:
            sorgente = approvazione or {}
            report = crea_report_excel(
                scelta,
                anno,
                mese,
                sorgente.get("personale", dati["personale"]),
                sorgente.get("regole", dati["regole"]),
                salvato,
                sorgente.get("calendario", salvato["calendario"]),
                storico_minuti=storico_minuti_approvati(dati, anno, mese),
                storico_mesi=storico_mesi_approvati(dati, anno, mese),
                assegnazioni_precedenti=calendario_mese_precedente(dati, anno, mese),
            )
        except ValueError as errore:
            st.warning(f"{errore} Premi “Genera calendario Excel” per aggiornarlo.")
        else:
            nome_file = re.sub(r"[^A-Za-z0-9_-]+", "_", scelta).strip("_")
            st.download_button(
                "Scarica report Excel modificabile",
                data=report,
                file_name=f"turni_{nome_file}_{anno}_{mese:02d}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"download_{suffisso}",
            )


# ---------- Pagina ----------
st.set_page_config(page_title="Gestione turni sanitari", layout="wide")
st.title("Gestione turni sanitari")

if "messaggio" in st.session_state:
    st.success(st.session_state.pop("messaggio"))
for avviso in st.session_state.pop("avvisi", []):
    st.warning(avviso)

# Barra laterale: scelta e creazione della struttura
with st.sidebar:
    st.header("Struttura")

    with st.form("nuova_struttura", clear_on_submit=True):
        nuovo_nome = st.text_input("Nuova struttura (es. Ospedale, RSA)")
        crea = st.form_submit_button("Crea")

    if crea:
        nome_pulito = "".join(c for c in nuovo_nome if c.isalnum() or c in " -_").strip()
        if not nome_pulito:
            st.warning("Inserisci un nome valido.")
        elif percorso(nome_pulito).exists():
            st.warning("Esiste già una struttura con questo nome.")
        else:
            salva_dati(nome_pulito, carica_dati(nome_pulito))
            st.session_state["struttura_attiva"] = nome_pulito

    strutture = elenco_strutture()
    scelta = (
        st.selectbox("Struttura attiva", strutture, key="struttura_attiva")
        if strutture
        else None
    )

if not scelta:
    st.info("Crea una struttura dalla barra laterale per cominciare.")
    st.stop()

dati = carica_dati(scelta)
regole = dati["regole"]

tab_personale, tab_regole, tab_mese = st.tabs(["Personale", "Regole", "Pianificazione mensile"])

# ---------- Scheda Personale ----------
with tab_personale:
    st.subheader(f"Personale: {scelta}")
    st.caption(
        "Aggiungi una riga per ogni persona e spunta i turni che può fare. "
        "Per impostazione predefinita, il calendario cerca di assegnare almeno un turno festivo a tutti; "
        "deseleziona Almeno 1 festivo per indicare un'eccezione. "
        "Puoi modificare qualsiasi cella in qualsiasi momento e premere Salva. "
        "Per eliminare una riga, selezionala e premi Canc. "
        "Il calendario assegna al massimo un turno al giorno per persona. "
        "Il nome identifica la persona nelle assenze e nei turni predeterminati: "
        "se lo cambi, ricontrolla anche quelle schede."
    )

    versione_p = st.session_state.get("versione_personale", 0)

    modificato = st.data_editor(
        normalizza(pd.DataFrame(dati["personale"])),
        num_rows="dynamic",
        hide_index=True,
        key=f"editor_{scelta}_{versione_p}",
        column_config={
            "Nome": st.column_config.TextColumn("Nome", required=True),
            "Categoria": st.column_config.SelectboxColumn(
                "Categoria", options=CATEGORIE, default="Di ruolo", required=True
            ),
            "Almeno 1 festivo": st.column_config.CheckboxColumn(
                "Almeno 1 festivo", default=True
            ),
            "Mattina": st.column_config.CheckboxColumn("Mattina (M)", default=True),
            "Pomeriggio": st.column_config.CheckboxColumn("Pomeriggio (P)", default=True),
            "Notte": st.column_config.CheckboxColumn("Notte (N)", default=True),
            "Ore settimanali": st.column_config.NumberColumn(
                "Ore settimanali", min_value=0.0, max_value=60.0, step=0.5, default=38.0
            ),
        },
    )

    if st.button("Salva personale", type="primary"):
        da_salvare = normalizza(modificato)
        da_salvare = da_salvare[da_salvare["Nome"].str.strip() != ""].reset_index(drop=True)

        if da_salvare["Nome"].str.strip().duplicated().any():
            st.error("Ci sono nomi duplicati: correggili prima di salvare.")
        else:
            dati["personale"] = json.loads(da_salvare.to_json(orient="records"))
            st.session_state.pop("alternative", None)
            invalida_calendari(dati)
            salva_dati(scelta, dati)
            st.session_state["versione_personale"] = versione_p + 1
            st.session_state["messaggio"] = f"Personale salvato ({len(da_salvare)} persone)."
            st.rerun()

# ---------- Scheda Regole ----------
with tab_regole:
    st.subheader(f"Regole: {scelta}")
    versione_r = st.session_state.get("versione_regole", 0)

    st.markdown("**Orari dei turni**")
    st.caption(
        "Formato HH:MM. Un turno che finisce dopo la mezzanotte, come la notte, "
        "viene gestito in automatico."
    )
    turni_mod = st.data_editor(
        pd.DataFrame(regole["turni"]),
        hide_index=True,
        disabled=["Turno"],
        key=f"turni_{scelta}_{versione_r}",
        column_config={
            "Inizio": st.column_config.TextColumn(
                "Inizio", required=True, validate=FORMATO_ORA.pattern
            ),
            "Fine": st.column_config.TextColumn(
                "Fine", required=True, validate=FORMATO_ORA.pattern
            ),
        },
    )

    durate = []
    for riga in turni_mod.itertuples():
        if FORMATO_ORA.match(str(riga.Inizio)) and FORMATO_ORA.match(str(riga.Fine)):
            durate.append(f"{riga.Turno}: {formatta(durata_minuti(riga.Inizio, riga.Fine))}")
    if durate:
        st.caption("Durata dei turni: " + " · ".join(durate))

    st.markdown("**Persone richieste per turno**")
    st.caption(
        "Per i festivi vale anche la notte che precede un giorno festivo o una "
        "domenica (per esempio il sabato notte)."
    )
    copertura_mod = st.data_editor(
        pd.DataFrame(regole["copertura"]),
        hide_index=True,
        disabled=["Turno"],
        key=f"copertura_{scelta}_{versione_r}",
        column_config={
            "Feriale": st.column_config.NumberColumn(
                "Giorni feriali", min_value=0, max_value=50, step=1, format="%d"
            ),
            "Festivo": st.column_config.NumberColumn(
                "Giorni festivi", min_value=0, max_value=50, step=1, format="%d"
            ),
        },
    )

    pomeriggio_feriale_minimo = st.number_input(
        "Minimo persone al pomeriggio nei giorni feriali",
        min_value=0,
        max_value=50,
        value=int(regole.get("pomeriggio_feriale_minimo", 1)),
        step=1,
        help=(
            "La copertura Feriale del pomeriggio è quella desiderata. "
            "Il generatore scende fino a questo minimo solo se il personale non basta "
            "a coprire tutti i turni: il pomeriggio è il primo turno a essere ridotto."
        ),
        key=f"pomeriggio_minimo_{scelta}_{versione_r}",
    )

    riposo = st.number_input(
        "Ore minime tra la fine di un turno e l'inizio del successivo",
        min_value=0,
        max_value=48,
        step=1,
        value=int(regole["riposo_minimo_ore"]),
        key=f"riposo_{scelta}_{versione_r}",
    )

    scostamento_max_turni = st.number_input(
        "Scostamento massimo dal monte ore mensile (in turni)",
        min_value=0,
        max_value=10,
        step=1,
        value=int(regole.get("scostamento_max_turni", 2)),
        help=(
            "Il monte ore del mese è Ore settimanali × (giorni del mese ÷ 7). "
            "Nessuno può superarlo di più di questo numero di turni."
        ),
        key=f"scostamento_{scelta}_{versione_r}",
    )

    max_turni_consecutivi = st.number_input(
        "Turni di lavoro consecutivi prima di un giorno di riposo",
        min_value=1,
        max_value=14,
        step=1,
        value=int(regole.get("max_turni_consecutivi", 6)),
        help=(
            "Dopo la notte c'è lo smonto: il giorno dopo non si lavora, ma lo smonto "
            "non conta come giorno di riposo."
        ),
        key=f"consecutivi_{scelta}_{versione_r}",
    )

    if st.button("Salva regole", type="primary"):
        orari_validi = all(
            FORMATO_ORA.match(str(valore))
            for colonna in ("Inizio", "Fine")
            for valore in turni_mod[colonna]
        )
        if not orari_validi:
            st.error("Controlla gli orari: devono avere il formato HH:MM, per esempio 08:00.")
        else:
            copertura = copertura_mod.copy()
            for colonna in ("Feriale", "Festivo"):
                copertura[colonna] = (
                    pd.to_numeric(copertura[colonna], errors="coerce").fillna(0).astype(int)
                )
            copertura_pomeriggio_feriale = next(
                (
                    int(riga["Feriale"])
                    for riga in copertura.to_dict(orient="records")
                    if riga.get("Turno") == "Pomeriggio"
                ),
                0,
            )
            if pomeriggio_feriale_minimo > copertura_pomeriggio_feriale:
                st.error(
                    "Il minimo di persone al pomeriggio feriale non può superare "
                    "la copertura feriale del pomeriggio."
                )
                st.stop()
            regole["turni"] = json.loads(turni_mod.to_json(orient="records"))
            regole["copertura"] = json.loads(copertura.to_json(orient="records"))
            regole["pomeriggio_feriale_minimo"] = int(pomeriggio_feriale_minimo)
            regole["riposo_minimo_ore"] = int(riposo)
            regole["scostamento_max_turni"] = int(scostamento_max_turni)
            regole["max_turni_consecutivi"] = int(max_turni_consecutivi)
            st.session_state.pop("alternative", None)
            invalida_calendari(dati)
            salva_dati(scelta, dati)
            st.session_state["versione_regole"] = versione_r + 1
            st.session_state["messaggio"] = "Regole salvate."
            st.rerun()

# ---------- Scheda Pianificazione mensile ----------
with tab_mese:
    st.subheader(f"Pianificazione mensile: {scelta}")
    nomi = [
        str(riga["Nome"]).strip()
        for riga in dati["personale"]
        if str(riga.get("Nome", "")).strip()
    ]
    mostra_pianificazione(scelta, dati, nomi)
