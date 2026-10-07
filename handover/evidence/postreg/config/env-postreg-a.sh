# POST-REG-R1 comparison arm A (Kriya 69465d3, venv-postreg): source before any kriya command.
export KRIYA_STATE_DIR=/Users/sriramnanduri/kriya-m1-live/state-postreg-a KRIYA_LOG_DIR=/Users/sriramnanduri/kriya-m1-live/logs-postreg-a KRIYA_AUTHORITY_HOME=/Users/sriramnanduri/kriya-m1-live/authority
export KRIYA_STATIC_ANALYSIS_HOME=/Users/sriramnanduri/kriya-m1-live/static-analysis KRIYA_MCP_APPROVAL_HOME=/Users/sriramnanduri/kriya-m1-live/mcp-approvals
unset KRIYA_CERTIFICATION_HOME PYTHONPATH
export KRIYA_QUALIFICATION_HOME=/Users/sriramnanduri/kriya-m1-live/qual-view-postreg-a   # run-specific READ-ONLY qualification view
export PATH=/Users/sriramnanduri/kriya-m1-live/venv-postreg/bin:$PATH
