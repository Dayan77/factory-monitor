import { act, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { useLayoutEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const me = vi.fn();
vi.mock("@/services/api", () => ({ authApi: { me: () => me(), login: vi.fn() } }));

import ProtectedRoute from "./ProtectedRoute";
import { useAuthStore } from "@/store/auth";
import { ok } from "@/test/utils";

const USER = { id: "u1", name: "Admin", email: "a@x", role: "admin", tenant_id: "t1" };

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function renderAt(page: JSX.Element) {
  return render(
    <MemoryRouter initialEntries={["/tv"]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route path="/login" element={<p>login page</p>} />
        <Route element={<ProtectedRoute />}>
          <Route path="/tv" element={page} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("ProtectedRoute", () => {
  beforeEach(() => {
    me.mockReset();
    localStorage.clear();
    useAuthStore.setState({ user: null, loading: false });
  });
  afterEach(() => localStorage.clear());

  it("with a stored token, waits for /auth/me before mounting the page, and mounts it exactly once", async () => {
    localStorage.setItem("access_token", "tok");
    const d = deferred<ReturnType<typeof ok>>();
    me.mockReturnValue(d.promise);
    const mounts = vi.fn();
    const unmounts = vi.fn();
    function Page() {
      useLayoutEffect(() => { mounts(); return () => unmounts(); }, []);
      return <p>tv page</p>;
    }
    renderAt(<Page />);
    expect(screen.getByTestId("auth-pending")).toBeInTheDocument();
    expect(screen.queryByText("tv page")).toBeNull();
    expect(mounts).not.toHaveBeenCalled();

    await act(async () => { d.resolve(ok(USER)); await d.promise; });
    expect(await screen.findByText("tv page")).toBeInTheDocument();
    expect(mounts).toHaveBeenCalledTimes(1);
    expect(unmounts).not.toHaveBeenCalled();
  });

  it("keeps the page mounted when the auth store flips loading after the user is known", async () => {
    localStorage.setItem("access_token", "tok");
    useAuthStore.setState({ user: USER, loading: false });
    const unmounts = vi.fn();
    function Page() {
      useLayoutEffect(() => () => unmounts(), []);
      return <p>tv page</p>;
    }
    renderAt(<Page />);
    act(() => useAuthStore.setState({ loading: true }));
    expect(screen.getByText("tv page")).toBeInTheDocument();
    expect(unmounts).not.toHaveBeenCalled();
    expect(me).not.toHaveBeenCalled();
  });

  it("redirects to /login without a token", () => {
    renderAt(<p>tv page</p>);
    expect(screen.getByText("login page")).toBeInTheDocument();
    expect(me).not.toHaveBeenCalled();
  });

  it("redirects to /login when the stored token is rejected", async () => {
    localStorage.setItem("access_token", "bad");
    me.mockRejectedValue(new Error("401"));
    renderAt(<p>tv page</p>);
    expect(await screen.findByText("login page")).toBeInTheDocument();
    expect(localStorage.getItem("access_token")).toBeNull();
  });
});
