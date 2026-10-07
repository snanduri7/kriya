# POST-REG-R2 comparison arm B (Kriya 56ae8d3, venv-postreg2): source before any kriya command.
export KRIYA_STATE_DIR=/Users/sriramnanduri/kriya-m1-live/state-postreg2-b KRIYA_LOG_DIR=/Users/sriramnanduri/kriya-m1-live/logs-postreg2-b KRIYA_AUTHORITY_HOME=/Users/sriramnanduri/kriya-m1-live/authority
export KRIYA_STATIC_ANALYSIS_HOME=/Users/sriramnanduri/kriya-m1-live/static-analysis KRIYA_MCP_APPROVAL_HOME=/Users/sriramnanduri/kriya-m1-live/mcp-approvals
unset KRIYA_CERTIFICATION_HOME PYTHONPATH
export KRIYA_QUALIFICATION_HOME=/Users/sriramnanduri/kriya-m1-live/qual-view-postreg2-b   # run-specific READ-ONLY qualification view
export PATH=/Users/sriramnanduri/kriya-m1-live/venv-postreg2/bin:$PATH
