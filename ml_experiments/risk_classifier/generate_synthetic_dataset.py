"""Generate a deterministic synthetic multilingual risk-classification dataset.

The dataset is intentionally isolated from the running Lumen app. It contains
synthetic referral-style text only and is designed for a demo fine-tuning pass.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict
from pathlib import Path


LABELS = [
    "none",
    "self_harm_or_suicidality",
    "acute_crisis",
    "safeguarding_or_abuse",
    "unclear",
]
DEFAULT_SEED = 20260512
ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = ROOT / "data"


BASE_EXAMPLES: dict[str, list[tuple[str, str]]] = {
    "none": [
        ("en", "Adult referral for work stress and mild anxiety, no safety concerns reported."),
        ("en", "Client asks for CBT for panic before presentations and denies thoughts of self harm."),
        ("en", "GP referral for low mood after a breakup; risk was assessed and denied."),
        ("en", "Student wants help with sleep routine, exam stress, and motivation."),
        ("en", "Parent requests support for adolescent social anxiety, stable at home and school."),
        ("en", "Referral notes grief after bereavement with good family support and no acute risk."),
        ("pt", "Encaminhamento para ansiedade no trabalho, sem preocupacoes de seguranca relatadas."),
        ("pt", "Pessoa adulta procura apoio por stress e insonia, nega pensamentos de se magoar."),
        ("pt", "Pedido de terapia para ataques de panico em reunioes, risco avaliado como ausente."),
        ("pt", "Estudante quer apoio para organizacao, sono e ansiedade antes dos exames."),
        ("pt", "Encaminhamento por luto recente, com suporte familiar e sem risco agudo."),
        ("pt", "Cliente refere preocupacao geral e cansaco, mas diz sentir-se seguro em casa."),
    ],
    "self_harm_or_suicidality": [
        ("en", "Patient says they do not feel safe and might hurt themselves tonight."),
        ("en", "Referral mentions recent thoughts of ending life and needing urgent support."),
        ("en", "Client disclosed self-harm urges and cannot agree to stay safe."),
        ("en", "Email from family says the person has been saying they do not want to live."),
        ("en", "Caller reports suicidal thoughts, tearful, alone, and unsure they can wait."),
        ("en", "Intake form says: I keep thinking about hurting myself when the anxiety spikes."),
        ("pt", "Paciente diz que nao se sente seguro e pode magoar-se hoje a noite."),
        ("pt", "Encaminhamento refere pensamentos suicidas recentes e necessidade de apoio urgente."),
        ("pt", "Cliente partilhou impulsos de automutilacao e nao consegue garantir seguranca."),
        ("pt", "Familiar escreve que a pessoa tem dito que nao quer viver."),
        ("pt", "Chamada relata ideias suicidas, choro, isolamento e medo de esperar."),
        ("pt", "Formulario diz: penso em ferir-me quando a ansiedade aumenta."),
    ],
    "acute_crisis": [
        ("en", "Patient is currently in severe crisis, disoriented, and has not slept for days."),
        ("en", "Caller reports panic, shouting, and possible psychosis after stopping medication."),
        ("en", "Referral says the person is wandering outside confused and unable to care for self."),
        ("en", "Emergency appointment requested because client is escalating and cannot calm down."),
        ("en", "Family says patient is hearing voices, highly agitated, and needs same-day review."),
        ("en", "GP note: acute deterioration, no food for two days, crisis team may be needed."),
        ("pt", "Paciente esta em crise grave, desorientado e sem dormir ha varios dias."),
        ("pt", "Chamada refere panico, gritos e possivel psicose apos parar medicacao."),
        ("pt", "Encaminhamento diz que a pessoa anda confusa na rua e nao consegue cuidar de si."),
        ("pt", "Pedido urgente porque o cliente esta a escalar e nao consegue acalmar."),
        ("pt", "Familia refere vozes, agitacao intensa e necessidade de avaliacao no mesmo dia."),
        ("pt", "Nota do medico: deterioracao aguda, sem comer ha dois dias, possivel equipa de crise."),
    ],
    "safeguarding_or_abuse": [
        ("en", "Referral reports domestic violence at home and the client is afraid to return."),
        ("en", "Teacher is concerned a child may be unsafe due to bruises and disclosure of abuse."),
        ("en", "Client says partner controls phone, money, and has threatened them."),
        ("en", "Parent reports elder abuse concerns and coercion around medication."),
        ("en", "Intake notes possible sexual abuse disclosure requiring safeguarding review."),
        ("en", "Social worker asks for therapy but flags ongoing safety risk in the household."),
        ("pt", "Encaminhamento relata violencia domestica em casa e medo de regressar."),
        ("pt", "Professor esta preocupado que uma crianca esteja insegura apos revelar abuso."),
        ("pt", "Cliente diz que o parceiro controla telefone, dinheiro e fez ameacas."),
        ("pt", "Familiar relata suspeita de abuso de pessoa idosa e pressao sobre medicacao."),
        ("pt", "Formulario indica possivel abuso sexual que exige revisao de salvaguarda."),
        ("pt", "Assistente social pede terapia mas sinaliza risco continuo no agregado familiar."),
    ],
    "unclear": [
        ("en", "Referral text is fragmented: safe? not safe? client did not complete risk section."),
        ("en", "GP wrote 'risk maybe' but no details, patient unreachable for clarification."),
        ("en", "Message says the client is struggling and family is worried, safety not assessed."),
        ("en", "Intake has crossed-out notes about crisis but the final wording is unclear."),
        ("en", "Referral says 'urgent please' with no reason and no risk history included."),
        ("en", "Client denies immediate danger but also says things could change quickly."),
        ("pt", "Texto fragmentado: seguro? nao seguro? secao de risco incompleta."),
        ("pt", "Medico escreveu 'talvez risco' mas sem detalhes e paciente incontactavel."),
        ("pt", "Mensagem diz que o cliente esta mal e a familia preocupada, seguranca nao avaliada."),
        ("pt", "Formulario tem notas riscadas sobre crise, mas a versao final e pouco clara."),
        ("pt", "Encaminhamento diz 'urgente por favor' sem motivo nem historico de risco."),
        ("pt", "Cliente nega perigo imediato mas diz que as coisas podem mudar rapidamente."),
    ],
}


STYLE_TEMPLATES = [
    (
        "email",
        "Subject: new referral\nFrom: {source}\n\n{text}\nPreferred modality: {modality}. Availability: {availability}.",
    ),
    (
        "webform",
        "WEBFORM ENTRY\nReason for referral: {text}\nLanguage: {language}. Contact details: {contact}.",
    ),
    (
        "gp_letter",
        "GP referral note - presenting issue: {text} Please triage for appropriate pathway.",
    ),
    (
        "intake_note",
        "INTAKE SUMMARY\n{text}\nRisk field copied from source; spelling and detail may be incomplete.",
    ),
    (
        "sms",
        "Short message from referrer: {text} pls call when possible. {noise}",
    ),
    (
        "mixed_notes",
        "Copied notes:\n- concern: {text}\n- insurer: {insurer}\n- admin note: {noise}",
    ),
]


SOURCES = ["GP", "school counsellor", "family member", "self referral", "social worker", "private clinic"]
MODALITIES = ["online", "in person", "hybrid", "sem preferencia", "online se possivel"]
AVAILABILITY = ["Tuesday morning", "after 17:00", "flexible", "sexta de manha", "weekends only"]
CONTACTS = ["email present", "phone missing", "needs confirmation", "contacto parcial", "email + phone"]
INSURERS = ["Multicare", "AdvanceCare", "private pay", "insurance unclear", "seguro por confirmar"]
NOISE = [
    "DOB missing.",
    "Patient prefers Portuguese.",
    "No previous therapy listed.",
    "Referrer sent duplicate attachment.",
    "Texto copiado de email com erros.",
    "Need admin cleanup before booking.",
]


def _render_text(base_text: str, language: str, rng: random.Random) -> tuple[str, str]:
    style, template = rng.choice(STYLE_TEMPLATES)
    rendered = template.format(
        text=base_text,
        source=rng.choice(SOURCES),
        modality=rng.choice(MODALITIES),
        availability=rng.choice(AVAILABILITY),
        language="Portuguese" if language == "pt" else "English",
        contact=rng.choice(CONTACTS),
        insurer=rng.choice(INSURERS),
        noise=rng.choice(NOISE),
    )
    if rng.random() < 0.25:
        rendered = rendered.replace(".", " .").replace("  ", " ")
    if rng.random() < 0.20:
        rendered = f"{rendered}\n\nAdditional unstructured note: {rng.choice(NOISE)}"
    return style, rendered


def build_rows(rows_per_label: int, seed: int) -> list[dict[str, str]]:
    rng = random.Random(seed)
    rows: list[dict[str, str]] = []

    for label in LABELS:
        examples = BASE_EXAMPLES[label]
        for index in range(rows_per_label):
            language, base_text = examples[index % len(examples)]
            style, rendered = _render_text(base_text, language, rng)
            rows.append(
                {
                    "id": f"synthetic-{len(rows) + 1:04d}",
                    "language": language,
                    "source_style": style,
                    "label": label,
                    "text": rendered,
                }
            )

    rng.shuffle(rows)
    for index, row in enumerate(rows, start=1):
        row["id"] = f"synthetic-{index:04d}"
    return rows


def stratified_split(rows: list[dict[str, str]], test_ratio: float, seed: int) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    rng = random.Random(seed + 17)
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[row["label"]].append(row)

    train_rows: list[dict[str, str]] = []
    test_rows: list[dict[str, str]] = []
    for label in LABELS:
        group = list(groups[label])
        rng.shuffle(group)
        test_count = max(1, round(len(group) * test_ratio))
        test_rows.extend(group[:test_count])
        train_rows.extend(group[test_count:])

    rng.shuffle(train_rows)
    rng.shuffle(test_rows)
    return train_rows, test_rows


def write_jsonl(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["id", "language", "source_style", "label", "text"])
        writer.writeheader()
        writer.writerows(rows)


def label_counts(rows: list[dict[str, str]]) -> dict[str, int]:
    counts = {label: 0 for label in LABELS}
    for row in rows:
        counts[row["label"]] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic referral risk-classification data.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--rows-per-label", type=int, default=60)
    parser.add_argument("--test-ratio", type=float, default=0.20)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    if args.rows_per_label < 10:
        raise ValueError("--rows-per-label should be at least 10 for a useful stratified demo dataset.")
    if not 0.05 <= args.test_ratio <= 0.5:
        raise ValueError("--test-ratio must be between 0.05 and 0.5.")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = build_rows(rows_per_label=args.rows_per_label, seed=args.seed)
    train_rows, test_rows = stratified_split(rows, test_ratio=args.test_ratio, seed=args.seed)

    write_csv(args.output_dir / "synthetic_referrals.csv", rows)
    write_jsonl(args.output_dir / "synthetic_referrals.jsonl", rows)
    write_jsonl(args.output_dir / "train.jsonl", train_rows)
    write_jsonl(args.output_dir / "test.jsonl", test_rows)

    summary = {
        "seed": args.seed,
        "labels": LABELS,
        "total_rows": len(rows),
        "train_rows": len(train_rows),
        "test_rows": len(test_rows),
        "label_counts": label_counts(rows),
        "train_label_counts": label_counts(train_rows),
        "test_label_counts": label_counts(test_rows),
    }
    (args.output_dir / "dataset_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

