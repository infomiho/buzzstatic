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
