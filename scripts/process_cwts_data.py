
import argparse
import json
import logging
import time
from pathlib import Path

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def load_tsv(data_dir: Path, filename: str) -> pd.DataFrame:
    path = data_dir / filename
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return pd.read_csv(path, sep='\t')

def build_main_field_map(data_dir: Path) -> dict:
    main_field_df = load_tsv(data_dir, "main_field.tsv")
    return dict(zip(main_field_df["main_field_id"], main_field_df["main_field"]))

def primary_main_field(link_df: pd.DataFrame, main_field_map: dict, id_col: str) -> pd.DataFrame:
    primary_df = link_df[link_df["is_primary_main_field"] == True].copy()
    primary_df["main_field"] = primary_df["main_field_id"].map(main_field_map)
    return primary_df[[id_col, "main_field_id", "main_field", "weight"]]

def build_clusters(data_dir: Path) -> dict:
    main_field_map = build_main_field_map(data_dir)

    macro_df = load_tsv(data_dir, "macro_cluster.tsv")
    macro_main = primary_main_field(load_tsv(data_dir, "macro_cluster_main_field.tsv"), main_field_map, "macro_cluster_id")
    macro = macro_df.merge(macro_main, on="macro_cluster_id", how="left")

    meso_df = load_tsv(data_dir, "meso_cluster.tsv")
    meso_main = primary_main_field(load_tsv(data_dir, "meso_cluster_main_field.tsv"), main_field_map, "meso_cluster_id")
    meso = meso_df.merge(meso_main, on="meso_cluster_id", how="left")

    micro_df = load_tsv(data_dir, "micro_cluster.tsv")
    micro_main = primary_main_field(load_tsv(data_dir, "micro_cluster_main_field.tsv"), main_field_map, "micro_cluster_id")
    micro = micro_df.merge(micro_main, on="micro_cluster_id", how="left")

    return {
        "macro_clusters": macro.to_dict(orient="records"),
        "meso_clusters": meso.to_dict(orient="records"),
        "micro_clusters": micro.to_dict(orient="records")
    }

def build_source_map(data_dir: Path) -> list:
    meso_source_df = load_tsv(data_dir, "meso_cluster_source.tsv")
    micro_source_df = load_tsv(data_dir, "micro_cluster_source.tsv")
    main_field_map = build_main_field_map(data_dir)
    meso_main_field_df = load_tsv(data_dir, "meso_cluster_main_field.tsv")
    meso_main_field_df = meso_main_field_df[meso_main_field_df["is_primary_main_field"] == True].copy()
    meso_main_field_df["main_field"] = meso_main_field_df["main_field_id"].map(main_field_map)
    meso_field_map = dict(zip(meso_main_field_df["meso_cluster_id"], meso_main_field_df["main_field"]))
    micro_main_field_df = load_tsv(data_dir, "micro_cluster_main_field.tsv")
    micro_main_field_df = micro_main_field_df[micro_main_field_df["is_primary_main_field"] == True].copy()
    micro_main_field_df["main_field"] = micro_main_field_df["main_field_id"].map(main_field_map)
    micro_field_map = dict(zip(micro_main_field_df["micro_cluster_id"], micro_main_field_df["main_field"]))
    micro_df = load_tsv(data_dir, "micro_cluster.tsv")
    micro_label_map = dict(zip(micro_df["micro_cluster_id"], micro_df["short_label"]))

    sources = {}

    for row in meso_source_df.itertuples(index=False):
        source_id = int(row.source_id)
        entry = sources.setdefault(source_id, {
            "source_id": f"S{source_id}",
            "meso_clusters": [],
            "micro_clusters": []
        })
        entry["meso_clusters"].append({
            "meso_cluster_id": int(row.meso_cluster_id),
            "n_works": int(row.n_works)
        })

    for row in micro_source_df.itertuples(index=False):
        source_id = int(row.source_id)
        entry = sources.setdefault(source_id, {
            "source_id": f"S{source_id}",
            "meso_clusters": [],
            "micro_clusters": []
        })
        entry["micro_clusters"].append({
            "micro_cluster_id": int(row.micro_cluster_id),
            "n_works": int(row.n_works)
        })

    source_list = list(sources.values())
    for entry in source_list:
        entry["meso_n_works"] = int(sum(item["n_works"] for item in entry["meso_clusters"]))
        entry["micro_n_works"] = int(sum(item["n_works"] for item in entry["micro_clusters"]))
        entry["total_n_works"] = entry["meso_n_works"] + entry["micro_n_works"]
        top_meso = sorted(entry["meso_clusters"], key=lambda x: x["n_works"], reverse=True)[:5]
        fields = []
        for item in top_meso:
            field = meso_field_map.get(item["meso_cluster_id"])
            if field and field not in fields:
                fields.append(field)
        if not fields:
            top_micro_for_fields = sorted(entry["micro_clusters"], key=lambda x: x["n_works"], reverse=True)[:5]
            for item in top_micro_for_fields:
                field = micro_field_map.get(item["micro_cluster_id"])
                if field and field not in fields:
                    fields.append(field)
        entry["fields"] = fields
        top_micro = sorted(entry["micro_clusters"], key=lambda x: x["n_works"], reverse=True)[:5]
        micro_topics = []
        for item in top_micro:
            label = micro_label_map.get(item["micro_cluster_id"])
            if label and label not in micro_topics:
                micro_topics.append(label)
        entry["micro_topics"] = micro_topics

    source_list.sort(key=lambda x: x["total_n_works"], reverse=True)
    return source_list

def resolve_sources(sources: list, email: str | None, limit: int | None, sleep: float, timeout: float) -> list:
    headers = {}
    if email:
        headers["User-Agent"] = f"mailto:{email}"

    for index, source in enumerate(sources):
        if limit is not None and index >= limit:
            break
        url = f"https://api.openalex.org/sources/{source['source_id']}"
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            if response.status_code == 200:
                data = response.json()
                source["name"] = data.get("display_name")
                source["issn_l"] = data.get("issn_l")
                source["publisher"] = data.get("host_organization_name")
                source["homepage_url"] = data.get("homepage_url")
                source["type"] = data.get("type")
        except Exception as e:
            logger.warning(f"Resolve source failed: {source['source_id']} {e}")
        if sleep > 0:
            time.sleep(sleep)
    return sources

def write_json(path: Path, payload: dict | list):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

def build_journal_knowledge(sources: list) -> dict:
    journals = []
    for source in sources:
        if not source.get("name"):
            continue
        journals.append({
            "name": source.get("name"),
            "source_id": source.get("source_id"),
            "issn_l": source.get("issn_l"),
            "publisher": source.get("publisher"),
            "fields": source.get("fields", []),
            "micro_topics": source.get("micro_topics", []),
            "coreness_score": source.get("total_n_works", 0)
        })
    return {"journals": journals}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data/classification_openalex_2024aug")
    parser.add_argument("--output-dir", default="data")
    parser.add_argument("--resolve-sources", action="store_true")
    parser.add_argument("--resolve-limit", type=int, default=None)
    parser.add_argument("--resolve-sleep", type=float, default=0.15)
    parser.add_argument("--resolve-timeout", type=float, default=10.0)
    parser.add_argument("--email", type=str, default=None)
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir)

    clusters = build_clusters(data_dir)
    sources = build_source_map(data_dir)

    if args.resolve_sources:
        sources = resolve_sources(
            sources,
            email=args.email,
            limit=args.resolve_limit,
            sleep=args.resolve_sleep,
            timeout=args.resolve_timeout
        )

    write_json(output_dir / "cwts_clusters.json", clusters)
    write_json(output_dir / "cwts_source_map.json", {"sources": sources})
    if args.resolve_sources:
        write_json(output_dir / "cwts_journal_knowledge.json", build_journal_knowledge(sources))

    logger.info(f"Clusters saved to {output_dir / 'cwts_clusters.json'}")
    logger.info(f"Sources saved to {output_dir / 'cwts_source_map.json'}")
    if args.resolve_sources:
        logger.info(f"Journal knowledge saved to {output_dir / 'cwts_journal_knowledge.json'}")

if __name__ == "__main__":
    main()
