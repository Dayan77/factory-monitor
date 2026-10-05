import { isAxiosError } from "axios";
import clsx from "clsx";

interface Props {
  title?: string;
  error?: unknown;
  onRetry?: () => void;
  className?: string;
}

function describe(error: unknown): string {
  if (isAxiosError(error)) {
    if (!error.response) return "Servidor indisponível. Verifique sua conexão.";
    return `O servidor respondeu com erro ${error.response.status}.`;
  }
  if (error instanceof Error && error.message) return error.message;
  return "Erro inesperado.";
}

export default function ErrorState({ title = "Não foi possível carregar os dados", error, onRetry, className }: Props) {
  return (
    <div
      role="alert"
      className={clsx(
        "flex flex-col items-center justify-center gap-2 rounded-xl border border-red-500/30 bg-red-500/5 px-5 py-8 text-center",
        className,
      )}
    >
      <svg viewBox="0 0 20 20" fill="currentColor" className="size-6 text-red-400" aria-hidden="true">
        <path fillRule="evenodd" d="M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0zm-8-5a.75.75 0 0 1 .75.75v4.5a.75.75 0 0 1-1.5 0v-4.5A.75.75 0 0 1 10 5zm0 10a1 1 0 1 0 0-2 1 1 0 0 0 0 2z" clipRule="evenodd" />
      </svg>
      <p className="text-sm font-medium text-red-300">{title}</p>
      <p className="text-xs text-gray-400">{describe(error)}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-2 rounded-lg border border-gray-700 bg-gray-800 px-4 py-2 text-xs font-medium text-gray-200 transition-colors hover:bg-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
        >
          Tentar novamente
        </button>
      )}
    </div>
  );
}
