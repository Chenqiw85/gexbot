import { addTicker, buildTickerUpdateRequestBody, fetchTickers, updateTicker, type AppTicker } from "@/lib/api";

async function apiRegistryContract() {
  const listResult: { tickers: AppTicker[] } = await fetchTickers();
  const created: AppTicker = await addTicker("msft", 0.005);
  const disabled: AppTicker = await updateTicker("msft", { enabled: false });
  const updatedYield: AppTicker = await updateTicker("msft", { dividendYield: 0.012 });
  const updateBody: { enabled?: boolean; dividend_yield?: number } = buildTickerUpdateRequestBody({
    enabled: false,
    dividendYield: 0.012
  });

  return { listResult, created, disabled, updatedYield, updateBody };
}

void apiRegistryContract;
