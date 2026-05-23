import os
from datetime import datetime, timezone

import psycopg2
from psycopg2 import sql
from pgvector.psycopg2 import register_vector
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from tqdm import tqdm

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("Falta DATABASE_URL en tu archivo .env")

# Modelo local pequeño: 384 dimensiones
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
model = SentenceTransformer(MODEL_NAME)

TABLES = [
    {
        "table": "Enterprise",
        "id_col": "id",
        "text_cols": ["name", "description"],
        "embedding_col": "embedding",
    },
    {
        "table": "Products",
        "id_col": "id",
        "text_cols": ["name", "description"],
        "embedding_col": "embedding",
    },
    {
        "table": "Lead",
        "id_col": "id",
        "text_cols": ["description", "email", "phone"],
        "embedding_col": "embedding",
    },
]


def build_text(row_dict, text_cols):
    parts = []
    for col in text_cols:
        value = row_dict.get(col)
        if value:
            parts.append(f"{col}: {value}")
    return "\n".join(parts).strip()


def fetch_rows(conn, table_cfg, limit=None):
    table = table_cfg["table"]
    id_col = table_cfg["id_col"]
    embedding_col = table_cfg["embedding_col"]
    text_cols = table_cfg["text_cols"]

    selected_cols = [id_col] + text_cols

    query = sql.SQL("""
        select {cols}
        from {table}
        where {embedding_col} is null
    """).format(
        cols=sql.SQL(", ").join(map(sql.Identifier, selected_cols)),
        table=sql.Identifier(table),
        embedding_col=sql.Identifier(embedding_col),
    )

    if limit:
        query += sql.SQL(" limit {}").format(sql.Literal(limit))

    with conn.cursor() as cur:
        cur.execute(query)
        rows = cur.fetchall()

    result = []
    for row in rows:
        row_dict = dict(zip(selected_cols, row))
        text = build_text(row_dict, text_cols)

        if text:
            result.append({
                "id": row_dict[id_col],
                "text": text,
            })

    return result


def update_embedding(conn, table_cfg, row_id, embedding):
    table = table_cfg["table"]
    id_col = table_cfg["id_col"]
    embedding_col = table_cfg["embedding_col"]

    query = sql.SQL("""
        update {table}
        set {embedding_col} = %s,
            updated_at = %s
        where {id_col} = %s
    """).format(
        table=sql.Identifier(table),
        embedding_col=sql.Identifier(embedding_col),
        id_col=sql.Identifier(id_col),
    )

    with conn.cursor() as cur:
        cur.execute(query, (embedding, datetime.now(timezone.utc), row_id))


def process_table(conn, table_cfg, batch_size=64, limit=None):
    rows = fetch_rows(conn, table_cfg, limit=limit)

    print(f"\nTabla {table_cfg['table']}: {len(rows)} filas por procesar")

    for i in tqdm(range(0, len(rows), batch_size)):
        batch = rows[i:i + batch_size]
        texts = [r["text"] for r in batch]

        embeddings = model.encode(
            texts,
            normalize_embeddings=True,
            batch_size=batch_size,
            show_progress_bar=False,
        )

        for row, emb in zip(batch, embeddings):
            update_embedding(conn, table_cfg, row["id"], emb.tolist())

        conn.commit()


def main():
    conn = psycopg2.connect(DATABASE_URL)
    register_vector(conn)

    try:
        for table_cfg in TABLES:
            process_table(conn, table_cfg, batch_size=64)
    finally:
        conn.close()

    print("\nListo. Embeddings guardados en Supabase.")


if __name__ == "__main__":
    main()