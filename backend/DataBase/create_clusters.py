import os
import json
import math
from datetime import datetime, timezone

import numpy as np
import psycopg2
from psycopg2 import sql
from pgvector.psycopg2 import register_vector
from dotenv import load_dotenv
from sklearn.cluster import KMeans
from sklearn.metrics import pairwise_distances

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("Falta DATABASE_URL en .env")


# AJUSTA ESTOS VALORES si tus enums usan nombres diferentes.
# Deben coincidir exactamente con los enum_value de Supabase.
TABLE_CONFIGS = [
    {
        "table": "Lead",
        "category": "lead",
        "member_type": "lead",
        "label_cols": ["description", "email", "phone"],
    },
    {
        "table": "Products",
        "category": "product",
        "member_type": "product",
        "label_cols": ["name", "description"],
    },
    {
        "table": "Enterprise",
        "category": "enterprise",
        "member_type": "enterprise",
        "label_cols": ["name", "description"],
    },
]

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
MAX_CLUSTERS_PER_TABLE = 8
RANDOM_STATE = 42


def normalize_vector(v):
    arr = np.array(v, dtype=np.float32)
    norm = np.linalg.norm(arr)

    if norm == 0:
        return arr

    return arr / norm


def choose_k(n):
    """
    Heurística rápida para hackatón.
    Si tienes pocos datos, crea pocos clusters.
    """
    if n <= 0:
        return 0

    if n <= 3:
        return 1

    return min(MAX_CLUSTERS_PER_TABLE, max(2, int(math.sqrt(n))))


def build_label(row_dict, label_cols):
    parts = []

    for col in label_cols:
        value = row_dict.get(col)
        if value:
            value = str(value).replace("\n", " ").strip()
            parts.append(value)

    text = " | ".join(parts)

    if len(text) > 180:
        text = text[:180] + "..."

    return text or "Sin descripción"


def fetch_items(conn, cfg):
    table = cfg["table"]
    label_cols = cfg["label_cols"]

    selected_cols = ["id", "embedding"] + label_cols

    query = sql.SQL("""
        select {cols}
        from {table}
        where embedding is not null
    """).format(
        cols=sql.SQL(", ").join(map(sql.Identifier, selected_cols)),
        table=sql.Identifier(table),
    )

    with conn.cursor() as cur:
        cur.execute(query)
        rows = cur.fetchall()

    items = []

    for row in rows:
        row_dict = dict(zip(selected_cols, row))
        embedding = row_dict["embedding"]

        if embedding is None:
            continue

        vector = normalize_vector(embedding)

        if len(vector) != EMBEDDING_DIM:
            raise ValueError(
                f"{table}.{row_dict['id']} tiene dimensión {len(vector)}, "
                f"pero esperaba {EMBEDDING_DIM}"
            )

        items.append({
            "id": row_dict["id"],
            "embedding": vector,
            "label": build_label(row_dict, label_cols),
        })

    return items


def delete_old_clusters_for_category(conn, category):
    """
    Para poder correr el script varias veces durante el hackatón,
    borramos los clusters previos de esa categoría.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            delete from public."ClusterMembers"
            where fk_cluster in (
                select id
                from public."Clusters"
                where category = %s
            );
            """,
            (category,),
        )

        cur.execute(
            """
            delete from public."Clusters"
            where category = %s;
            """,
            (category,),
        )

    conn.commit()


def insert_cluster(conn, cfg, cluster_index, centroid, members):
    sample_labels = [m["label"] for m in members[:5]]

    name = f"{cfg['category']} cluster {cluster_index + 1}"
    description = (
        f"Cluster automático de {len(members)} registros tipo {cfg['category']}. "
        f"Ejemplos: " + " || ".join(sample_labels)
    )

    metadata = {
        "source_table": cfg["table"],
        "model": MODEL_NAME,
        "embedding_dim": EMBEDDING_DIM,
        "n_members": len(members),
        "sample_ids": [str(m["id"]) for m in members[:10]],
        "created_by": "local_kmeans_script",
    }

    centroid_id = members[0]["id"] if members else None

    with conn.cursor() as cur:
        cur.execute(
            """
            insert into public."Clusters"
                (category, description, centroid_id, name, field_key, embedding, metadata, created_at, updated_at)
            values
                (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            returning id;
            """,
            (
                cfg["category"],
                description,
                centroid_id,
                name,
                "embedding",
                centroid.tolist(),
                json.dumps(metadata),
                datetime.now(timezone.utc),
                datetime.now(timezone.utc),
            ),
        )

        cluster_id = cur.fetchone()[0]

    return cluster_id


def insert_cluster_members(conn, cfg, cluster_id, centroid, members):
    if not members:
        return

    member_embeddings = np.array([m["embedding"] for m in members])

    # Distancia coseno: 0 = muy similar, 1 = menos similar
    distances = pairwise_distances(
        member_embeddings,
        centroid.reshape(1, -1),
        metric="cosine",
    ).flatten()

    with conn.cursor() as cur:
        for member, distance in zip(members, distances):
            cur.execute(
                """
                insert into public."ClusterMembers"
                    (fk_cluster, member_id, member_type, distance, created_at)
                values
                    (%s, %s, %s, %s, %s);
                """,
                (
                    cluster_id,
                    member["id"],
                    cfg["member_type"],
                    float(distance),
                    datetime.now(timezone.utc),
                ),
            )


def process_table(conn, cfg):
    print(f"\nProcesando tabla {cfg['table']}...")

    items = fetch_items(conn, cfg)
    n = len(items)

    print(f"Registros con embedding: {n}")

    if n == 0:
        print("No hay registros para clusterizar.")
        return

    delete_old_clusters_for_category(conn, cfg["category"])

    k = choose_k(n)
    print(f"Número de clusters elegido: {k}")

    embeddings = np.array([item["embedding"] for item in items])

    if k == 1:
        labels = np.zeros(n, dtype=int)
        centers = np.array([normalize_vector(np.mean(embeddings, axis=0))])
    else:
        kmeans = KMeans(
            n_clusters=k,
            random_state=RANDOM_STATE,
            n_init=10,
        )

        labels = kmeans.fit_predict(embeddings)
        centers = np.array([normalize_vector(c) for c in kmeans.cluster_centers_])

    for cluster_index in range(k):
        members = [
            item
            for item, label in zip(items, labels)
            if label == cluster_index
        ]

        if not members:
            continue

        centroid = centers[cluster_index]

        cluster_id = insert_cluster(
            conn=conn,
            cfg=cfg,
            cluster_index=cluster_index,
            centroid=centroid,
            members=members,
        )

        insert_cluster_members(
            conn=conn,
            cfg=cfg,
            cluster_id=cluster_id,
            centroid=centroid,
            members=members,
        )

        conn.commit()

        print(
            f"Cluster {cluster_index + 1}: {len(members)} miembros guardados."
        )


def main():
    conn = psycopg2.connect(DATABASE_URL)
    register_vector(conn)

    try:
        for cfg in TABLE_CONFIGS:
            process_table(conn, cfg)
    finally:
        conn.close()

    print("\nListo. Clusters y ClusterMembers generados.")


if __name__ == "__main__":
    main()