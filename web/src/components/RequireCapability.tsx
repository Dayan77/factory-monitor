import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useAuthStore } from "@/store/auth";
import { Card } from "@/components/ui/Card";
import EmptyState from "@/components/ui/EmptyState";
import { can, type Capability } from "@/utils/maintenance";

/** Renders children only for users with the capability; otherwise an explicit "no permission" state. */
export default function RequireCapability({ cap, children }: { cap: Capability; children: ReactNode }) {
  const user = useAuthStore((s) => s.user);
  if (can(user, cap)) return <>{children}</>;
  return (
    <div className="mx-auto max-w-3xl">
      <Card>
        <EmptyState title="Sem permissão para esta tela"
          description="Seu perfil não dá acesso a esta área da manutenção. Peça ao administrador para ajustar seus papéis."
          action={<Link to="/dashboard" className="text-body font-medium text-ok hover:underline">Voltar ao Raio-X</Link>} />
      </Card>
    </div>
  );
}
