"use client";

/**
 * Minimum-viable login page for harmony-code (M5 Task 5.2).
 *
 * Posts to ``/api/auth/sign-in/email`` with ``credentials: "include"`` so
 * the backend's ``harmony_session`` cookie is stored. On success redirects
 * to ``/workspace/chats``. There is deliberately no sign-up link — user
 * creation is admin-CLI-only (``python -m app.admin create-user``).
 *
 * UX polish (styling, i18n, error shaping, remember-me, SSO) is M6. The
 * goal here is a page that lets a developer actually use the app after
 * strict auth lands.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";

import { getBackendBaseURL } from "@/core/config";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await fetch(`${getBackendBaseURL()}/api/auth/sign-in/email`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        setError(res.status === 401 ? "Invalid email or password" : `Error ${res.status}`);
        setLoading(false);
        return;
      }
      router.push("/workspace/chats");
    } catch (err) {
      setError((err as Error).message || "Network error");
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm space-y-4 rounded-lg border p-6 shadow-sm"
      >
        <h1 className="text-xl font-semibold">Sign in</h1>
        <p className="text-sm text-muted-foreground">
          harmony-code is single-tenant. Accounts are provisioned by an admin.
        </p>
        <div className="space-y-2">
          <label htmlFor="email" className="block text-sm font-medium">
            Email
          </label>
          <input
            id="email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded border px-3 py-2"
          />
        </div>
        <div className="space-y-2">
          <label htmlFor="password" className="block text-sm font-medium">
            Password
          </label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded border px-3 py-2"
          />
        </div>
        {error && (
          <div role="alert" className="text-sm text-red-600">
            {error}
          </div>
        )}
        <button
          type="submit"
          disabled={loading}
          className="w-full rounded bg-black px-4 py-2 text-white disabled:opacity-50"
        >
          {loading ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </div>
  );
}
