"""Stand-in for the CDISC CORE CLI in tests. Writes a report in CORE's JSON format (sheet names and
fields as in cdisc_rules_engine/services/reporting/sdtm_report_data.py, v1.0.0)."""
import json
import os
import sys

args = sys.argv[1:]
if args[0] == "version":
    print("1.0.0")
elif args[0] == "list-rules":
    rid = args[args.index("-r") + 1]
    print("loading cache...")
    print(json.dumps([{
        "core_id": rid, "description": "AESTDTC must be on or before AEENDTC", "rule_type": "Record Data",
        "sensitivity": "Record", "executability": "Fully Executable", "domains": {"Include": ["AE"]},
        "conditions": {"all": [{"name": "AESTDTC", "operator": "date_greater_than", "value": {"target": "AEENDTC"}}]},
        "actions": [{"name": "generate_dataset_error_objects", "params": {"message": "AESTDTC is after AEENDTC"}}],
        "authorities": [{"Organization": "CDISC", "Standards": [{"Name": "SDTMIG", "Version": "3.4", "References": [
            {"Rule_Identifier": {"Id": "CG0001"}, "Citations": [{"Document": "IG v3.4", "Section": "4.4.7",
                                                                  "Cited_Guidance": "Start date must not be after end date"}]}]}]}],
    }]))
elif args[0] == "validate":
    out = args[args.index("-o") + 1]
    with open(os.environ["FAKE_CORE_REPORT"]) as f:
        rep = json.load(f)
    rules = [args[i + 1] for i, a in enumerate(args) if a == "-r"]
    if rules:
        rep["Issue_Details"] = [d for d in rep["Issue_Details"] if d["core_id"] in rules]
    with open(out, "w") as f:
        json.dump(rep, f)
    with open(os.environ["FAKE_CORE_ARGS"], "w") as f:
        json.dump(args, f)
else:
    sys.exit(2)
