"""Re-run of one P1 mutant whose first form was inert (campaign defect: it
looked for ``alias`` at the record's top level; records keep it under
``fingerprint.alias``). The original SURVIVED entry stays in
p1_mutation_results.json; this corrected mutant's result is
p1_mutation_rerun_runtime_identity.json."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import p1_mutation_campaign as campaign  # noqa: E402 - sibling evidence script

MC = "kriya/core/model_capabilities.py"
campaign.M = [("derive", "ignore-runtime-identity (corrected)", MC,
               "        record = mq.load_record(fingerprint.digest, settings)\n",
               "        record = mq.load_record(fingerprint.digest, settings) or next((r for r in mq._stored_records() "
               "if (r.get('fingerprint') or {}).get('alias') == model), None)\n"
               "        fingerprint = type('F', (), {'digest': (record or {}).get('fingerprint_digest'), "
               "'exact': True})() if record else fingerprint\n")]
campaign.OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "p1_mutation_rerun_runtime_identity.json")
sys.exit(campaign.main())
