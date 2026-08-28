"""One-shot, credential-free RQData trading-parameter exporter."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import rqdatac


def _hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()).hexdigest().upper()


def _records(frame):
    output=[]
    for row in frame.reset_index().to_dict(orient="records"):
        clean={}
        for key,value in row.items():
            if pd.isna(value): clean[str(key)]=None
            elif isinstance(value,(pd.Timestamp,datetime)): clean[str(key)]=value.isoformat()
            elif isinstance(value,(np.integer,np.floating)): clean[str(key)]=value.item()
            else: clean[str(key)]=value
        output.append(clean)
    return output


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--input",required=True)
    parser.add_argument("--output",required=True)
    args=parser.parse_args()
    source=json.loads(Path(args.input).read_text(encoding="utf-8"))
    if source.get("content_sha256") != "E0E7001141EED0C8D1A46E58F47C875ADBC628BF62B491773C5A8BBF71D4F731":
        raise RuntimeError("v541_source_freeze_hash_mismatch")
    contracts=sorted({row["contract"] for rows in source["commodity_raw"]["dominant"].values() for row in rows})
    rqdatac.init()
    frame=rqdatac.futures.get_trading_parameters(
        contracts,start_date=source["date_range"]["start"],end_date=source["date_range"]["end"],
        fields=["commission_type","open_commission","close_commission","close_commission_today","trade_unit","price_unit"],market="cn"
    )
    rows=_records(frame)
    payload={
        "schema_version":"rqdata-futures-trading-parameters-v541/1.0",
        "provider":"RQData",
        "source_content_sha256":source["content_sha256"],
        "retrieved_at":datetime.now(timezone.utc).isoformat(),
        "contract_count":len(contracts),
        "rows":rows,
        "query":{"api":"futures.get_trading_parameters","fields":["commission_type","open_commission","close_commission","close_commission_today","trade_unit","price_unit"]},
        "deployment_allowed":False,
    }
    payload["content_sha256"]=_hash(payload)
    target=Path(args.output).resolve(); target.parent.mkdir(parents=True,exist_ok=True)
    temp=target.with_suffix(target.suffix+".tmp")
    temp.write_text(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False),encoding="utf-8")
    temp.replace(target)
    print(json.dumps({"status":"ok","rows":len(rows),"contract_count":len(contracts),"content_sha256":payload["content_sha256"]},sort_keys=True))


if __name__=="__main__":
    main()
