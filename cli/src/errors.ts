export class CliError extends Error {
  constructor(
    message: string,
    public tip?: string,
    public code?: string
  ) {
    super(message);
    this.name = "CliError";
  }
}

export interface ErrorJson {
  code: string;
  message: string;
  tip: string | null;
}

export function errorJson(error: unknown): ErrorJson {
  if (error instanceof CliError) {
    return { code: error.code ?? "ERROR", message: error.message, tip: error.tip ?? null };
  }
  return { code: "ERROR", message: error instanceof Error ? error.message : String(error), tip: null };
}
