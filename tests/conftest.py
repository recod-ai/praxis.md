import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="praxis-test-")
os.environ["PRAXIS_WORKSPACE_DIR"] = os.path.join(_tmp, "workspace")
os.environ["PRAXIS_STATE_DB"] = os.path.join(_tmp, "state.db")
os.environ.pop("PRAXIS_AUTH_MODE", None)
