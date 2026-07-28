import { fetchHistory, persistRunRows, type GexRun } from "@/lib/api";

async function checkpointContract() {
  const checkpoint: GexRun = await persistRunRows(123);
  const history = await fetchHistory("SPY", "all", "2026-07-27", 2000);
  return { persisted: checkpoint.persist_strike_rows, history };
}

void checkpointContract;
