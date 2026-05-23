import os
import json
from datetime import datetime, date
from decimal import Decimal

import psycopg2
from dotenv import load_dotenv
from pgvector.psycopg2 import register_vector


load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("Falta DATABASE_URL en .env")


OUTPUT_FILE = "cold_call_payloads_clean.json"

# Para cold calls reales recomiendo 1 producto principal por lead.
# Si quieres varias opciones por lead, cambia esto a 2 o 3.
TOP_PRODUCTS_PER_LEAD = 1


def json_safe(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, Decimal):
        return float(value)

    return str(value)


def clean_text(value, max_len=900):
    if value is None:
        return None

    text = str(value).replace("\n", " ").strip()

    if not text:
        return None

    if len(text) > max_len:
        return text[:max_len] + "..."

    return text


def clean_json_field(value, max_items=6):
    """
    Convierte jsonb a una lista limpia que el agente sí pueda usar.
    """
    if value is None:
        return []

    if isinstance(value, list):
        result = []
        for item in value[:max_items]:
            if isinstance(item, str):
                result.append(clean_text(item, 250))
            elif isinstance(item, dict):
                result.append(clean_text(json.dumps(item, ensure_ascii=False), 250))
            else:
                result.append(clean_text(str(item), 250))
        return [x for x in result if x]

    if isinstance(value, dict):
        result = []
        for key, val in list(value.items())[:max_items]:
            if val is None:
                continue
            result.append(clean_text(f"{key}: {val}", 250))
        return [x for x in result if x]

    text = clean_text(value, 250)
    return [text] if text else []


def first_non_empty(*values):
    for value in values:
        value = clean_text(value)
        if value:
            return value
    return None


def build_opening_line(lead_company_name, product_name, product_company_name):
    if lead_company_name and product_name and product_company_name:
        return (
            f"Hola, te llamo porque creo que {lead_company_name} podría beneficiarse "
            f"de {product_name} de {product_company_name}. Quería entender si este "
            f"tipo de solución tiene sentido para ustedes en este momento."
        )

    if product_name and product_company_name:
        return (
            f"Hola, te llamo para compartirte {product_name} de {product_company_name}. "
            f"Quería entender si este tipo de solución podría ser relevante para ustedes."
        )

    if product_name:
        return (
            f"Hola, te llamo para compartirte {product_name}. "
            f"Quería entender si este tipo de solución podría ser relevante para ustedes."
        )

    return (
        "Hola, te llamo para entender si podemos ayudarles con una solución que parece "
        "alinearse con las necesidades de su empresa."
    )


def build_main_angle(lead_description, lead_company_description, product_name, product_description):
    lead_context = first_non_empty(lead_description, lead_company_description)

    if lead_context and product_description:
        return (
            f"Conectar la situación del lead con el valor del producto. "
            f"Contexto del lead: {lead_context} "
            f"Producto sugerido: {product_name}. "
            f"Descripción del producto: {product_description}"
        )

    if product_description:
        return (
            f"Presentar {product_name} como una solución relevante. "
            f"Descripción del producto: {product_description}"
        )

    return f"Presentar {product_name} de forma consultiva y validar necesidad real antes de vender."


def build_why_this_product(lead_description, lead_company_description, product_name, product_description, benefits, pain_points):
    reasons = []

    if lead_description:
        reasons.append(
            f"El lead tiene este contexto relevante: {lead_description}"
        )

    if lead_company_description:
        reasons.append(
            f"La empresa del lead se describe así: {lead_company_description}"
        )

    if product_description:
        reasons.append(
            f"{product_name} parece relevante porque ofrece: {product_description}"
        )

    if pain_points:
        reasons.append(
            "El producto está relacionado con estos dolores o necesidades: "
            + "; ".join(pain_points[:3])
        )

    if benefits:
        reasons.append(
            "Los beneficios que conviene destacar son: "
            + "; ".join(benefits[:3])
        )

    if not reasons:
        reasons.append(
            f"El producto {product_name} fue seleccionado como el mejor match semántico para este lead."
        )

    return reasons


def fetch_matches(conn):
    query = """
        with ranked_matches as (
            select
                l.description as lead_description,
                l.contact_info as lead_contact_info,
                l.phone as lead_phone,
                l.email as lead_email,

                le.name as lead_company_name,
                le.description as lead_company_description,

                p.name as product_name,
                p.description as product_description,
                p.target_profile as product_target_profile,
                p.pain_points as product_pain_points,
                p.benefits as product_benefits,
                p.objections as product_objections,

                pe.name as product_company_name,
                pe.description as product_company_description,

                row_number() over (
                    partition by l.id
                    order by l.embedding <=> p.embedding asc
                ) as match_rank

            from public."Lead" l
            join public."Products" p
                on p.embedding is not null
               and p.active = true
            left join public."Enterprise" le
                on le.id = l.fk_enterprise
            left join public."Enterprise" pe
                on pe.id = p.fk_enterprise
            where l.embedding is not null
        )
        select *
        from ranked_matches
        where match_rank <= %s
        order by lead_company_name nulls last, match_rank;
    """

    with conn.cursor() as cur:
        cur.execute(query, (TOP_PRODUCTS_PER_LEAD,))
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description]

    return [dict(zip(columns, row)) for row in rows]


def build_clean_payload(row):
    lead_description = clean_text(row["lead_description"], 700)
    lead_company_description = clean_text(row["lead_company_description"], 700)

    product_name = clean_text(row["product_name"], 200) or "Producto recomendado"
    product_description = clean_text(row["product_description"], 900)

    product_company_name = clean_text(row["product_company_name"], 200)
    product_company_description = clean_text(row["product_company_description"], 700)

    benefits = clean_json_field(row["product_benefits"])
    pain_points = clean_json_field(row["product_pain_points"])
    objections = clean_json_field(row["product_objections"])
    target_profile = clean_json_field(row["product_target_profile"])

    lead_company_name = clean_text(row["lead_company_name"], 200)

    payload = {
        "lead": {
            "phone": row["lead_phone"],
            "email": row["lead_email"],
            "context": lead_description,
            "extra_contact_info": row["lead_contact_info"] or {},
            "company": {
                "name": lead_company_name,
                "description": lead_company_description,
            },
        },

        "product_to_sell": {
            "name": product_name,
            "company": product_company_name,
            "description": product_description,
            "target_customer": target_profile,
            "pain_points_it_solves": pain_points,
            "main_benefits": benefits,
            "expected_objections": objections,
        },

        "sales_strategy": {
            "why_this_product_fits_this_lead": build_why_this_product(
                lead_description=lead_description,
                lead_company_description=lead_company_description,
                product_name=product_name,
                product_description=product_description,
                benefits=benefits,
                pain_points=pain_points,
            ),

            "main_sales_angle": build_main_angle(
                lead_description=lead_description,
                lead_company_description=lead_company_description,
                product_name=product_name,
                product_description=product_description,
            ),

            "opening_line": build_opening_line(
                lead_company_name=lead_company_name,
                product_name=product_name,
                product_company_name=product_company_name,
            ),

            "discovery_questions": [
                "¿Este problema o necesidad es relevante para ustedes actualmente?",
                "¿Cómo están resolviendo esto hoy?",
                "¿Qué tan prioritario sería mejorar esta área en los próximos meses?",
                "¿Qué tendría que pasar para que consideraran una solución como esta?",
                "¿Quién más tendría que participar en la decisión?",
            ],

            "value_points_to_emphasize": benefits[:4] if benefits else [
                "Aterrizar el valor del producto al problema específico del lead.",
                "Validar necesidad antes de vender agresivamente.",
                "Explicar el beneficio en términos concretos para su empresa.",
            ],

            "objection_handling": objections[:4] if objections else [
                "Si dice que no tiene tiempo: pedir solo 30 segundos para validar si tiene sentido.",
                "Si dice que ya tienen solución: preguntar qué tan bien les está funcionando.",
                "Si dice que no le interesa: preguntar si el problema no existe o si no es prioridad ahora.",
            ],

            "call_goal": (
                "Validar interés real, identificar necesidad, confirmar si la persona es decisora "
                "y conseguir siguiente paso: demo, reunión o callback."
            ),
        },
    }

    return payload


def remove_empty_values(obj):
    """
    Limpia campos vacíos para que el JSON final no traiga basura.
    """
    if isinstance(obj, dict):
        cleaned = {}

        for key, value in obj.items():
            cleaned_value = remove_empty_values(value)

            if cleaned_value in [None, "", [], {}]:
                continue

            cleaned[key] = cleaned_value

        return cleaned

    if isinstance(obj, list):
        cleaned_list = [remove_empty_values(item) for item in obj]
        return [item for item in cleaned_list if item not in [None, "", [], {}]]

    return obj


def main():
    conn = psycopg2.connect(DATABASE_URL)
    register_vector(conn)

    try:
        rows = fetch_matches(conn)

        payloads = [
            remove_empty_values(build_clean_payload(row))
            for row in rows
        ]

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(
                payloads,
                f,
                ensure_ascii=False,
                indent=2,
                default=json_safe,
            )

        print(f"Payloads generados: {len(payloads)}")
        print(f"Archivo generado: {OUTPUT_FILE}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()