import { buildAnalyzeRequestBody, type Scope } from "@/lib/api";

const defaultBody: {
  ticker: string;
  scope: Scope;
  persist_strike_rows: false;
  allow_chain_capture: false;
} = buildAnalyzeRequestBody("SPY", "all");

const explicitBody: {
  ticker: string;
  scope: Scope;
  persist_strike_rows: false;
  allow_chain_capture: true;
} = buildAnalyzeRequestBody("QQQ", "0dte", { allowChainCapture: true });

void defaultBody;
void explicitBody;
