"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Ban,
  CheckCircle2,
  Copy,
  Download,
  FileText,
  Folder,
  KeyRound,
  Laptop,
  Loader2,
  Plus,
  RefreshCw,
  Save,
  Search,
  ServerCog,
  Shield,
  Trash2,
  UserPlus,
  Users,
  Edit,
} from "lucide-react";

import Header from "@/components/Header";
import PolicyEditor from "@/components/PolicyEditor";
import { apiFetch } from "@/lib/api";

type Dashboard = {
  tokens: Record<string, number>;
  jobs: Record<string, number>;
  users: number;
  devices: number;
  exams: number;
};

type TokenRow = {
  id: string;
  hint: string;
  label: string;
  status: string;
  expires_at: string | null;
  redeemed_at: string | null;
  device_id: string | null;
  created_at: string;
  owner_user_id: string | null;
  exam_count: number;
  exam_limit: number;
  assigned_role: string;
  max_devices: number;
  device_count: number;
  group_id: string | null;
  exportable: boolean;
  owner_name: string | null;
  owner_email: string | null;
};

type TokenGroup = {
  id: string;
  name: string;
  total: number;
  counts: Record<string, number>;
  exportable_count: number;
};

type DeviceRow = {
  id: string;
  name: string;
  platform: string;
  user: string;
  activated_at: string;
  last_seen_at: string;
  revoked_at: string | null;
};

type UserRow = {
  id: string;
  display_name: string;
  email: string | null;
  role: string;
  status: string;
  device_count: number;
  device_limit: number;
  exam_count: number;
  exam_limit: number | null;
  created_at: string;
};

export default function AdminPage() {
  const router = useRouter();
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [tokens, setTokens] = useState<TokenRow[]>([]);
  const [tokenGroups, setTokenGroups] = useState<TokenGroup[]>([]);
  const [ungroupedTotal, setUngroupedTotal] = useState(0);
  const [devices, setDevices] = useState<DeviceRow[]>([]);
  const [users, setUsers] = useState<UserRow[]>([]);
  const [tab, setTab] = useState<"tokens" | "users" | "devices" | "policies">("tokens");
  const [count, setCount] = useState(1);
  const [label, setLabel] = useState("");
  const [examLimit, setExamLimit] = useState(5);
  const [tokenRole, setTokenRole] = useState<"user" | "teacher" | "student">("user");
  const [maxDevices, setMaxDevices] = useState(1);
  const [generated, setGenerated] = useState<string[]>([]);
  const [generatedGroupId, setGeneratedGroupId] = useState<string | null>(null);
  const [createGroupId, setCreateGroupId] = useState("");
  const [selectedGroupId, setSelectedGroupId] = useState("all");
  const [selectedTokenIds, setSelectedTokenIds] = useState<string[]>([]);
  const [moveGroupId, setMoveGroupId] = useState("");
  const [tokenStatus, setTokenStatus] = useState("");
  const [tokenSearch, setTokenSearch] = useState("");
  const [tokenPage, setTokenPage] = useState(1);
  const [tokenPages, setTokenPages] = useState(1);
  const [tokenTotal, setTokenTotal] = useState(0);
  const [bulkDeletingTokens, setBulkDeletingTokens] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function tokenListUrl(page = tokenPage) {
    const params = new URLSearchParams({ page: String(page), page_size: "50" });
    if (selectedGroupId !== "all") params.set("group_id", selectedGroupId);
    if (tokenStatus) params.set("status", tokenStatus);
    if (tokenSearch.trim()) params.set("search", tokenSearch.trim());
    return `/api/v1/admin/tokens?${params}`;
  }

  async function loadTokens(page = tokenPage) {
    const response = await apiFetch(tokenListUrl(page), { cache: "no-store" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || "Không tải được token");
    setTokens(payload.items || []);
    setTokenPage(payload.page || 1);
    setTokenPages(payload.pages || 1);
    setTokenTotal(payload.total || 0);
    setSelectedTokenIds([]);
  }

  // User CRUD Modal state
  const [userModal, setUserModal] = useState<{
    open: boolean;
    mode: "create" | "edit";
    id?: string;
    display_name: string;
    email: string;
    role: string;
    status: string;
    exam_limit: number;
  }>({
    open: false,
    mode: "create",
    display_name: "",
    email: "",
    role: "user",
    status: "active",
    exam_limit: 5,
  });

  // Policy Editor state
  const [policyKey, setPolicyKey] = useState<"terms" | "privacy">("terms");
  const [policyTitle, setPolicyTitle] = useState("");
  const [policyContent, setPolicyContent] = useState("");
  const [policySaving, setPolicySaving] = useState(false);
  const [policyPreview, setPolicyPreview] = useState(false);
  const [passwordModal, setPasswordModal] = useState({
    open: false,
    current_password: "",
    new_password: "",
    new_password_confirmation: "",
  });
  const [resetPasswordModal, setResetPasswordModal] = useState({
    open: false,
    user_id: "",
    user_name: "",
    new_password: "",
    new_password_confirmation: "",
  });

  async function load() {
    try {
      const [dashboardResponse, tokensResponse, groupsResponse, devicesResponse, usersResponse] = await Promise.all([
        apiFetch("/api/v1/admin/dashboard", { cache: "no-store" }),
        apiFetch(tokenListUrl(), { cache: "no-store" }),
        apiFetch("/api/v1/admin/token-groups", { cache: "no-store" }),
        apiFetch("/api/v1/admin/devices", { cache: "no-store" }),
        apiFetch("/api/v1/admin/users", { cache: "no-store" }),
      ]);
      if (dashboardResponse.status === 401) {
        router.replace("/login");
        setLoading(false);
        return;
      }
      if (dashboardResponse.status === 403) {
        router.replace("/my-exams");
        return;
      }
      const [dashboardData, tokenData, groupData, deviceData, usersData] = await Promise.all([
        dashboardResponse.json(),
        tokensResponse.json(),
        groupsResponse.json(),
        devicesResponse.json(),
        usersResponse.json(),
      ]);
      if (!dashboardResponse.ok) throw new Error(dashboardData.detail || "Không tải được Admin");
      setDashboard(dashboardData);
      setTokens(tokenData.items || []);
      setTokenPage(tokenData.page || 1);
      setTokenPages(tokenData.pages || 1);
      setTokenTotal(tokenData.total || 0);
      setTokenGroups(groupData.items || []);
      setUngroupedTotal(groupData.ungrouped?.total || 0);
      setDevices(deviceData.items || []);
      setUsers(usersData.items || []);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Có lỗi xảy ra");
    } finally {
      setLoading(false);
    }
  }

  async function loadPolicy(key: "terms" | "privacy") {
    try {
      const res = await apiFetch(`/api/v1/policies/${key}`);
      if (res.ok) {
        const data = await res.json();
        setPolicyTitle(data.title || "");
        setPolicyContent(data.rendered_html || data.content || "");
        setPolicyPreview(false);
      }
    } catch {
      //
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (tab === "policies") {
      loadPolicy(policyKey);
    }
  }, [tab, policyKey]);

  useEffect(() => {
    if (tab !== "tokens") return;
    const timer = window.setTimeout(() => {
      void loadTokens(1).catch((reason) =>
        setError(reason instanceof Error ? reason.message : "Không tải được token"),
      );
    }, 250);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedGroupId, tokenStatus, tokenSearch, tab]);

  async function createTokenGroup() {
    const name = window.prompt("Tên nhóm token, ví dụ: Trung Tâm A")?.trim();
    if (!name) return;
    setError(null);
    const response = await apiFetch("/api/v1/admin/token-groups", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      setError(payload.detail || "Không tạo được nhóm token");
      return;
    }
    setTokenGroups((current) => [...current, payload].sort((a, b) => a.name.localeCompare(b.name, "vi")));
    setCreateGroupId(payload.id);
    setSelectedGroupId(payload.id);
  }

  async function renameTokenGroup(group: TokenGroup) {
    const name = window.prompt("Tên mới của nhóm token", group.name)?.trim();
    if (!name || name === group.name) return;
    const response = await apiFetch(`/api/v1/admin/token-groups/${group.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) return setError(payload.detail || "Không đổi được tên nhóm");
    setTokenGroups((current) => current.map((item) => item.id === group.id ? payload : item));
  }

  async function deleteTokenGroup(group: TokenGroup) {
    if (!confirm(`Xóa folder "${group.name}"? ${group.total} token sẽ chuyển về Chưa phân nhóm.`)) return;
    const response = await apiFetch(`/api/v1/admin/token-groups/${group.id}`, { method: "DELETE" });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) return setError(payload.detail || "Không xóa được nhóm");
    if (selectedGroupId === group.id) setSelectedGroupId("ungrouped");
    if (createGroupId === group.id) setCreateGroupId("");
    setTokenGroups((current) => current.filter((item) => item.id !== group.id));
    await load();
  }

  async function moveSelectedTokens() {
    if (!selectedTokenIds.length) return;
    const response = await apiFetch("/api/v1/admin/tokens/group-membership", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token_ids: selectedTokenIds,
        group_id: moveGroupId || null,
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) return setError(payload.detail || "Không chuyển được token");
    await load();
  }

  async function deleteSelectedTokens() {
    if (!selectedTokenIds.length || bulkDeletingTokens) return;
    const selectedCount = selectedTokenIds.length;
    if (
      !confirm(
        `Xóa vĩnh viễn ${selectedCount} token đã chọn? Người dùng, thiết bị và đề thi đã tạo sẽ được giữ nguyên. Hành động này không thể hoàn tác.`,
      )
    ) {
      return;
    }
    setBulkDeletingTokens(true);
    setError(null);
    try {
      const response = await apiFetch("/api/v1/admin/tokens", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token_ids: selectedTokenIds }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "Không xóa được các token đã chọn");
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không xóa được các token đã chọn");
    } finally {
      setBulkDeletingTokens(false);
    }
  }

  async function exportTokenGroup(groupId: string) {
    setError(null);
    const response = await apiFetch(`/api/v1/admin/token-groups/${groupId}/export.xlsx`);
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      setError(payload.detail || "Không xuất được Excel");
      return;
    }
    const group = tokenGroups.find((item) => item.id === groupId);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${group?.name || "token-group"}-tokens.xlsx`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  async function createTokens() {
    setLoading(true);
    setError(null);
    try {
      const response = await apiFetch("/api/v1/admin/tokens", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          count,
          label,
          assigned_role: tokenRole,
          exam_limit: examLimit,
          max_devices: maxDevices,
          group_id: createGroupId || null,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Không sinh được token");
      setGenerated(payload.codes || []);
      setGeneratedGroupId(payload.group?.id || null);
      setLabel("");
      setMaxDevices(1);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Có lỗi xảy ra");
    } finally {
      setLoading(false);
    }
  }

  async function revoke(kind: "tokens" | "devices", id: string) {
    const target = kind === "tokens" ? "mã kích hoạt" : "thiết bị";
    if (!confirm(`Thu hồi ${target} này? Thiết bị liên kết sẽ phải kích hoạt lại.`)) {
      return;
    }
    setError(null);
    const response = await apiFetch(`/api/v1/admin/${kind}/${id}/revoke`, {
      method: "POST",
    });
    if (response.ok) {
      await load();
    } else {
      const payload = await response.json().catch(() => ({}));
      setError(payload.detail || "Không thu hồi được");
    }
  }

  async function deleteToken(tokenId: string) {
    if (
      !confirm(
        "Xóa vĩnh viễn token này khỏi hệ thống? Người dùng và đề thi đã tạo sẽ không bị xóa.",
      )
    ) {
      return;
    }
    setError(null);
    const response = await apiFetch(`/api/v1/admin/tokens/${tokenId}`, {
      method: "DELETE",
    });
    if (response.ok) {
      await load();
    } else {
      const payload = await response.json().catch(() => ({}));
      setError(payload.detail || "Không xóa được token");
    }
  }

  async function reissue(tokenId: string) {
    setError(null);
    try {
      const response = await apiFetch(`/api/v1/admin/tokens/${tokenId}/reissue`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ revoke_existing_devices: true }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Không cấp lại được mã");
      setGenerated([payload.code]);
      setGeneratedGroupId(null);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Có lỗi xảy ra");
    }
  }

  async function reissueUser(userId: string, name: string) {
    setError(null);
    try {
      const response = await apiFetch(`/api/v1/admin/users/${userId}/reissue-token`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ revoke_existing_devices: true, label: name }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Không sinh được key mới");
      setGenerated([payload.code]);
      setGeneratedGroupId(null);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Có lỗi xảy ra");
    }
  }

  async function updateDeviceLimit(userId: string, deviceLimit: number) {
    setError(null);
    try {
      const response = await apiFetch(`/api/v1/admin/users/${userId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_limit: Math.max(1, Math.min(2, deviceLimit)) }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "Không cập nhật được giới hạn thiết bị");
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không cập nhật được giới hạn thiết bị");
    }
  }

  // Save User
  async function handleSaveUser(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      if (userModal.mode === "create") {
        const res = await apiFetch("/api/v1/admin/users", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            display_name: userModal.display_name,
            email: userModal.email || null,
            role: userModal.role,
            status: userModal.status,
            exam_limit: ["admin", "student"].includes(userModal.role) ? null : userModal.exam_limit,
          }),
        });
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.detail || "Không tạo được người dùng");
      } else {
        const res = await apiFetch(`/api/v1/admin/users/${userModal.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            display_name: userModal.display_name,
            email: userModal.email || null,
            role: userModal.role,
            status: userModal.status,
            exam_limit: ["admin", "student"].includes(userModal.role) ? null : userModal.exam_limit,
          }),
        });
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.detail || "Không cập nhật được người dùng");
      }
      setUserModal({ ...userModal, open: false });
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Có lỗi xảy ra");
    }
  }

  async function handleAdminPasswordChange(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const response = await apiFetch("/api/v1/admin/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current_password: passwordModal.current_password,
          new_password: passwordModal.new_password,
          new_password_confirmation: passwordModal.new_password_confirmation,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "Không đổi được mật khẩu");
      setPasswordModal({
        open: false,
        current_password: "",
        new_password: "",
        new_password_confirmation: "",
      });
      alert("Đã đổi mật khẩu admin thành công.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không đổi được mật khẩu");
    }
  }

  async function handleResetUserPassword(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const response = await apiFetch(`/api/v1/admin/users/${resetPasswordModal.user_id}/password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          new_password: resetPasswordModal.new_password,
          new_password_confirmation: resetPasswordModal.new_password_confirmation,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "Không đặt lại được mật khẩu");
      setResetPasswordModal({
        open: false,
        user_id: "",
        user_name: "",
        new_password: "",
        new_password_confirmation: "",
      });
      alert(`Đã đặt lại mật khẩu cho ${resetPasswordModal.user_name}.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không đặt lại được mật khẩu");
    }
  }

  // Delete User
  async function handleDeleteUser(userId: string, name: string) {
    if (
      !confirm(
        `Xóa vĩnh viễn người dùng "${name}"? Nếu đây là Teacher, toàn bộ lớp học, bài thi/Public, lịch sử, đề trong CSDL và file trên MinIO của họ cũng sẽ bị xóa. Hành động này không thể hoàn tác.`,
      )
    )
      return;
    setError(null);
    try {
      const res = await apiFetch(`/api/v1/admin/users/${userId}`, { method: "DELETE" });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(payload.detail || "Không xóa được người dùng");
      await load();
      if (payload.storage_cleanup_complete === false) {
        setError(
          "Người dùng đã được xóa khỏi CSDL nhưng một số object MinIO chưa dọn được; hãy kiểm tra log API.",
        );
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không xóa được người dùng");
    }
  }

  // Save Policy
  async function handleSavePolicy() {
    setPolicySaving(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/v1/policies/${policyKey}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: policyTitle, content: policyContent, content_format: "html" }),
      });
      const payload = await res.json();
      if (!res.ok) throw new Error(payload.detail || "Không lưu được chính sách");
      alert("Đã lưu chính sách thành công!");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không lưu được chính sách");
    } finally {
      setPolicySaving(false);
    }
  }

  return (
    <main className="min-h-screen bg-slate-50">
      <Header />
      <div className="mx-auto max-w-[1500px] px-5 py-7 sm:px-8">
        <section className="overflow-hidden rounded-2xl border border-[#1f4e79]/15 bg-gradient-to-r from-[#122b49] via-[#1f4e79] to-[#27496d] px-6 py-6 text-white shadow-[0_14px_38px_rgba(31,78,121,0.18)] sm:px-8">
          <div className="flex flex-wrap items-end justify-between gap-5">
            <div>
              <span className="inline-flex items-center gap-2 rounded-full border border-white/20 bg-white/10 px-3 py-1 text-[11px] font-extrabold uppercase tracking-[0.18em]">
                <Shield className="h-3.5 w-3.5" /> Trung tâm điều hành
              </span>
              <h1 className="mt-3 text-3xl font-extrabold tracking-tight sm:text-4xl">
                Quản trị Examify
              </h1>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-blue-50/90">
                Theo dõi hệ thống, cấp quyền truy cập và quản lý dữ liệu vận hành tại một nơi.
              </p>
            </div>
            <div className="flex flex-col items-end gap-3">
              <div className="rounded-xl border border-white/20 bg-white/10 px-4 py-3 text-right backdrop-blur-sm">
              <p className="text-[11px] font-bold uppercase tracking-wider text-blue-100">Trạng thái</p>
              <p className="mt-1 inline-flex items-center gap-2 text-sm font-extrabold">
                <span className="h-2.5 w-2.5 rounded-full bg-emerald-300 shadow-[0_0_0_4px_rgba(110,231,183,0.16)]" />
                Hệ thống hoạt động
              </p>
              </div>
              <button
                type="button"
                onClick={() => setPasswordModal({
                  open: true,
                  current_password: "",
                  new_password: "",
                  new_password_confirmation: "",
                })}
                className="inline-flex items-center gap-2 rounded-xl border border-white/30 bg-white/15 px-4 py-2 text-xs font-extrabold text-white transition hover:bg-white/25"
              >
                <KeyRound className="h-4 w-4" /> Đổi mật khẩu admin
              </button>
            </div>
          </div>
        </section>

        <section className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          {[
            { label: "Token khả dụng", value: dashboard?.tokens.available || 0, hint: "Sẵn sàng cấp", icon: KeyRound },
            { label: "Người dùng", value: dashboard?.users || 0, hint: "Tất cả tài khoản", icon: Users },
            { label: "Thiết bị", value: dashboard?.devices || 0, hint: "Đang được quản lý", icon: Laptop },
            { label: "Kho đề", value: dashboard?.exams || 0, hint: "Đề đã lưu", icon: FileText },
            { label: "OCR đang chạy", value: dashboard?.jobs.processing || 0, hint: "Tác vụ xử lý", icon: ServerCog },
          ].map(({ label, value, hint, icon: Icon }) => (
            <div key={label} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-[0_5px_18px_rgba(31,78,121,0.07)]">
              <div className="flex items-start justify-between gap-3">
                <span className="rounded-xl bg-[#1f4e79]/8 p-2.5 text-[#1f4e79]">
                  <Icon className="h-5 w-5" />
                </span>
                <p className="text-2xl font-extrabold text-[#1f4e79]">{value}</p>
              </div>
              <p className="mt-3 text-sm font-extrabold text-slate-800">{label}</p>
              <p className="mt-0.5 text-xs text-slate-500">{hint}</p>
            </div>
          ))}
        </section>

        <div className="mt-6 space-y-5">
          <nav className="ui-card flex flex-wrap items-center justify-between gap-3 p-2.5" aria-label="Khu vực quản trị">
            <div className="flex flex-wrap gap-1.5">
              {[
                { key: "tokens", title: "Token & Nhóm", icon: KeyRound },
                { key: "users", title: "Người dùng", icon: Users },
                { key: "devices", title: "Thiết bị", icon: Laptop },
                { key: "policies", title: "Điều khoản & Chính sách", icon: FileText },
              ].map(({ key, title, icon: Icon }) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setTab(key as typeof tab)}
                  className={`inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-extrabold transition ${tab === key
                      ? "bg-[#1f4e79] text-white shadow-[0_5px_12px_rgba(31,78,121,0.2)]"
                      : "text-slate-600 hover:bg-slate-100 hover:text-[#1f4e79]"
                    }`}
                >
                  <Icon className="h-4 w-4" /> {title}
                </button>
              ))}
            </div>
            {tab === "users" && (
              <button
                onClick={() =>
                  setUserModal({
                    open: true,
                    mode: "create",
                    display_name: "",
                    email: "",
                    role: "user",
                    status: "active",
                    exam_limit: 5,
                  })
                }
                className="ui-btn-primary px-3 py-2 text-xs"
              >
                <UserPlus className="h-4 w-4" /> Thêm người dùng
              </button>
            )}
          </nav>

          {error && (
            <div className="rounded-xl border border-red-300 bg-red-50 px-4 py-3 text-sm font-semibold text-red-800">
              {error}
            </div>
          )}

          {tab === "tokens" && (
            <section className="ui-card p-5 sm:p-6">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="text-[11px] font-extrabold uppercase tracking-[0.16em] text-slate-400">Cấp quyền truy cập</p>
                  <h2 className="mt-1 text-xl font-extrabold text-[#1f4e79]">Sinh mã kích hoạt</h2>
                  <p className="mt-1 text-xs text-slate-500">Tạo tối đa 1.000 token/lần; mã không hết hạn, chỉ ngừng hoạt động khi Admin thu hồi hoặc xóa.</p>
                </div>
                <span className="rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-bold text-[#1f4e79]">
                  Bước 1 · Cấu hình token
                </span>
              </div>

              <div className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                <label className="block">
                  <span className="text-sm font-bold text-slate-700">Số lượng</span>
                  <input
                    type="number"
                    min={1}
                    max={1000}
                    value={count}
                    onChange={(e) => setCount(Math.min(1000, Math.max(1, Number(e.target.value) || 1)))}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-4 py-2 text-sm outline-none focus:border-[#1f4e79]"
                  />
                </label>
                <label className="block">
                  <span className="text-sm font-bold text-slate-700">Nhóm token</span>
                  <div className="mt-1 flex gap-2">
                    <select
                      value={createGroupId}
                      onChange={(event) => setCreateGroupId(event.target.value)}
                      className="min-w-0 flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-[#1f4e79]"
                    >
                      <option value="">Chưa phân nhóm</option>
                      {tokenGroups.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}
                    </select>
                    <button type="button" onClick={() => void createTokenGroup()} className="ui-btn-secondary px-3" title="Tạo nhóm mới">
                      <Plus className="h-4 w-4" />
                    </button>
                  </div>
                  {count > 1 && !createGroupId && <span className="mt-1 block text-xs font-semibold text-amber-700">Sinh hàng loạt cần chọn một nhóm.</span>}
                </label>
                {count === 1 && <label className="block">
                  <span className="text-sm font-bold text-slate-700">Nhãn / người nhận</span>
                  <input
                    value={label}
                    onChange={(e) => setLabel(e.target.value)}
                    placeholder="Ví dụ: Nguyễn Văn A"
                    className="mt-1 w-full rounded-lg border border-slate-300 px-4 py-2 text-sm outline-none focus:border-[#1f4e79]"
                  />
                </label>}
                <label className="block">
                  <span className="text-sm font-bold text-slate-700">Vai trò token</span>
                  <select
                    value={tokenRole}
                    onChange={(event) =>
                      setTokenRole(event.target.value as "user" | "teacher" | "student")
                    }
                    className="mt-1 w-full rounded-lg border border-slate-300 px-4 py-2 text-sm outline-none focus:border-[#1f4e79]"
                  >
                    <option value="user">Normal User</option>
                    <option value="teacher">Teacher</option>
                    <option value="student">Student</option>
                  </select>
                </label>
                {tokenRole !== "student" && <label className="block">
                  <span className="text-sm font-bold text-slate-700">
                    Số đề tối đa / người dùng
                  </span>
                  <input
                    type="number"
                    min={1}
                    max={10000}
                    value={examLimit}
                    onChange={(e) => setExamLimit(Number(e.target.value))}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-4 py-2 text-sm outline-none focus:border-[#1f4e79]"
                    required
                  />
                  <span className="mt-1 block text-xs text-slate-500">
                    Hạn mức được gắn vào người dùng khi token được kích hoạt.
                  </span>
                </label>}
                <label className="block">
                  <span className="text-sm font-bold text-slate-700">Thiết bị tối đa / Key</span>
                  <div className="mt-1 flex items-center gap-3">
                    <button
                      type="button"
                      onClick={() => setMaxDevices((value) => Math.max(1, value - 1))}
                      disabled={maxDevices <= 1}
                      className="h-10 w-10 rounded-lg border border-slate-300 text-lg font-bold text-[#1f4e79] disabled:cursor-not-allowed disabled:opacity-40"
                      aria-label="Giảm số thiết bị"
                    >
                      −
                    </button>
                    <span className="min-w-20 text-center text-sm font-bold text-slate-700">
                      {maxDevices} máy
                    </span>
                    <button
                      type="button"
                      onClick={() => setMaxDevices((value) => Math.min(2, value + 1))}
                      disabled={maxDevices >= 2}
                      className="h-10 w-10 rounded-lg border border-slate-300 text-lg font-bold text-[#1f4e79] disabled:cursor-not-allowed disabled:opacity-40"
                      aria-label="Tăng số thiết bị"
                    >
                      +
                    </button>
                  </div>
                  <span className="mt-1 block text-xs text-slate-500">
                    Mặc định 1 máy, có thể tăng tối đa 2 máy cho một Key.
                  </span>
                </label>
                <button onClick={createTokens} disabled={loading || (count > 1 && !createGroupId)} className="ui-btn-primary w-full self-end py-2.5 disabled:cursor-not-allowed disabled:opacity-50">
                  <Plus className="h-4 w-4" /> Sinh token
                </button>
              </div>

              {generated.length > 0 && (
                <div className="mt-6 rounded-xl border border-amber-300 bg-amber-50 p-4 text-xs">
                  <p className="font-bold text-amber-900">ĐÃ TẠO {generated.length} TOKEN</p>
                  <p className="mt-1 text-amber-800">Có thể tải lại Excel từ folder token bất kỳ lúc nào.</p>
                  <div className="mt-3 space-y-2 font-mono text-sm">
                    {generated.slice(0, 5).map((code) => (
                      <div key={code} className="flex items-center justify-between rounded-lg bg-white p-2 border">
                        <span>{code}</span>
                        <button
                          onClick={() => navigator.clipboard.writeText(code)}
                          className="p-1 text-slate-500 hover:text-slate-900"
                        >
                          <Copy className="h-4 w-4" />
                        </button>
                      </div>
                    ))}
                  </div>
                  {generated.length > 5 && <p className="mt-2 font-semibold text-amber-800">Và {generated.length - 5} token khác…</p>}
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button type="button" onClick={() => navigator.clipboard.writeText(generated.join("\n"))} className="ui-btn-secondary px-3 py-2 text-xs">
                      <Copy className="h-4 w-4" /> Sao chép tất cả
                    </button>
                    {generatedGroupId && <button type="button" onClick={() => void exportTokenGroup(generatedGroupId)} className="ui-btn-secondary px-3 py-2 text-xs">
                      <Download className="h-4 w-4" /> Xuất Excel
                    </button>}
                  </div>
                </div>
              )}
            </section>
          )}

          <section className="ui-card p-5 sm:p-6">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 pb-4">
              <div>
                <p className="text-[11px] font-extrabold uppercase tracking-[0.16em] text-slate-400">
                  {tab === "tokens" ? "Bước 2 · Quản lý và phân phối" : "Dữ liệu hệ thống"}
                </p>
                <h2 className="mt-1 text-lg font-extrabold text-[#1f4e79]">
                  {tab === "tokens"
                    ? "Kho token theo nhóm"
                    : tab === "users"
                      ? "Danh sách người dùng"
                      : tab === "devices"
                        ? "Thiết bị đã kích hoạt"
                        : "Nội dung pháp lý"}
                </h2>
              </div>
              {tab === "tokens" && (
                <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-bold text-slate-600">
                  {tokenTotal.toLocaleString("vi-VN")} token
                </span>
              )}
            </div>

            <div className="mt-4">
              {tab === "tokens" ? (
                <div className="grid gap-4 xl:grid-cols-[230px_minmax(0,1fr)]">
                  <aside className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                    <div className="mb-3 flex items-center justify-between gap-2">
                      <span className="text-xs font-extrabold uppercase tracking-wide text-slate-500">Folder token</span>
                      <button
                        type="button"
                        onClick={() => void createTokenGroup()}
                        className="rounded-lg border border-slate-300 bg-white p-1.5 text-[#1f4e79] hover:border-[#1f4e79]"
                        title="Tạo folder"
                      >
                        <Plus className="h-4 w-4" />
                      </button>
                    </div>
                    <div className="space-y-1">
                      {[
                        { id: "all", name: "Tất cả token", total: tokenGroups.reduce((sum, group) => sum + group.total, ungroupedTotal) },
                        { id: "ungrouped", name: "Chưa phân nhóm", total: ungroupedTotal },
                      ].map((folder) => (
                        <button
                          key={folder.id}
                          type="button"
                          onClick={() => { setSelectedGroupId(folder.id); setTokenPage(1); }}
                          className={`flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-2 text-left text-sm font-bold ${selectedGroupId === folder.id ? "bg-[#1f4e79] text-white" : "text-slate-700 hover:bg-white"
                            }`}
                        >
                          <span className="flex min-w-0 items-center gap-2"><Folder className="h-4 w-4 shrink-0" /><span className="truncate">{folder.name}</span></span>
                          <span className="text-xs opacity-75">{folder.total}</span>
                        </button>
                      ))}
                      {tokenGroups.map((group) => (
                        <div
                          key={group.id}
                          className={`group rounded-lg ${selectedGroupId === group.id ? "bg-[#1f4e79] text-white" : "text-slate-700 hover:bg-white"}`}
                        >
                          <button
                            type="button"
                            onClick={() => { setSelectedGroupId(group.id); setTokenPage(1); }}
                            className="flex w-full items-center justify-between gap-2 px-2.5 pb-1 pt-2 text-left text-sm font-bold"
                          >
                            <span className="flex min-w-0 items-center gap-2"><Folder className="h-4 w-4 shrink-0" /><span className="truncate">{group.name}</span></span>
                            <span className="text-xs opacity-75">{group.total}</span>
                          </button>
                          <div className="flex items-center justify-between px-2.5 pb-2 text-[11px] opacity-80">
                            <span>
                              {group.counts.available || 0} trống · {group.counts.redeemed || 0} đã dùng · {(group.counts.revoked || 0) + (group.counts.expired || 0)} khác
                            </span>
                            <span className="flex gap-1">
                              <button type="button" onClick={() => void exportTokenGroup(group.id)} className="rounded p-1 hover:bg-white/20" title="Xuất Excel"><Download className="h-3.5 w-3.5" /></button>
                              <button type="button" onClick={() => void renameTokenGroup(group)} className="rounded p-1 hover:bg-white/20" title="Đổi tên"><Edit className="h-3.5 w-3.5" /></button>
                              <button type="button" onClick={() => void deleteTokenGroup(group)} className="rounded p-1 hover:bg-red-500/20" title="Xóa folder"><Trash2 className="h-3.5 w-3.5" /></button>
                            </span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </aside>

                  <div className="min-w-0">
                    <div className="mb-3 flex flex-wrap items-center gap-2">
                      <label className="relative min-w-[220px] flex-1">
                        <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
                        <input
                          value={tokenSearch}
                          onChange={(event) => setTokenSearch(event.target.value)}
                          placeholder="Tìm mã, tên hoặc email..."
                          className="w-full rounded-lg border border-slate-300 py-2 pl-9 pr-3 text-sm outline-none focus:border-[#1f4e79]"
                        />
                      </label>
                      <select
                        value={tokenStatus}
                        onChange={(event) => setTokenStatus(event.target.value)}
                        className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                      >
                        <option value="">Mọi trạng thái</option>
                        <option value="available">Chưa dùng</option>
                        <option value="redeemed">Đã kích hoạt</option>
                        <option value="revoked">Đã thu hồi</option>
                      </select>
                      {selectedGroupId !== "all" && selectedGroupId !== "ungrouped" && (
                        <button type="button" onClick={() => void exportTokenGroup(selectedGroupId)} className="ui-btn-secondary px-3 py-2 text-xs">
                          <Download className="h-4 w-4" /> Xuất Excel
                        </button>
                      )}
                    </div>

                    {selectedTokenIds.length > 0 && (
                      <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm">
                        <strong>{selectedTokenIds.length} token đã chọn</strong>
                        <select value={moveGroupId} onChange={(event) => setMoveGroupId(event.target.value)} className="rounded-lg border border-slate-300 bg-white px-3 py-1.5">
                          <option value="">Chưa phân nhóm</option>
                          {tokenGroups.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}
                        </select>
                        <button type="button" onClick={() => void moveSelectedTokens()} className="ui-btn-primary px-3 py-1.5 text-xs">Chuyển folder</button>
                        <button
                          type="button"
                          onClick={() => void deleteSelectedTokens()}
                          disabled={bulkDeletingTokens}
                          className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-red-300 bg-white px-3 py-1.5 text-xs font-bold text-red-700 hover:bg-red-50 disabled:cursor-wait disabled:opacity-60"
                        >
                          {bulkDeletingTokens ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                          Xóa token đã chọn
                        </button>
                      </div>
                    )}

                    <div className="overflow-x-auto">
                      <table className="w-full min-w-[900px] text-left text-sm">
                        <thead className="text-xs uppercase text-slate-500">
                          <tr>
                            <th className="px-2 py-3">
                              <input
                                type="checkbox"
                                aria-label="Chọn tất cả token trên trang"
                                checked={tokens.length > 0 && tokens.every((token) => selectedTokenIds.includes(token.id))}
                                onChange={(event) => setSelectedTokenIds(event.target.checked ? tokens.map((token) => token.id) : [])}
                              />
                            </th>
                            <th className="px-3 py-3">Token</th>
                            <th className="px-3 py-3">Người nhận</th>
                            <th className="px-3 py-3">Vai trò</th>
                            <th className="px-3 py-3">Trạng thái</th>
                            <th className="px-3 py-3">Đã tạo / Giới hạn</th>
                            <th className="px-3 py-3">Thiết bị</th>
                            <th className="px-3 py-3">Ngày tạo</th>
                            <th className="px-3 py-3 text-right">Thao tác</th>
                          </tr>
                        </thead>
                        <tbody>
                          {tokens.map((token) => (
                            <tr key={token.id} className="border-t border-slate-200">
                              <td className="px-2 py-3"><input type="checkbox" aria-label={`Chọn token ${token.hint}`} checked={selectedTokenIds.includes(token.id)} onChange={() => setSelectedTokenIds((current) => current.includes(token.id) ? current.filter((id) => id !== token.id) : [...current, token.id])} /></td>
                              <td className="px-3 py-3">
                                <span className="block font-mono font-bold">••••-{token.hint}</span>
                                {!token.exportable && <span className="text-[11px] text-amber-700">Mã cũ không thể xuất</span>}
                              </td>
                              <td className="px-3 py-3">
                                <span className="block font-semibold">{token.owner_name || token.label || "Chưa kích hoạt"}</span>
                                {token.owner_email && <span className="text-xs text-slate-500">{token.owner_email}</span>}
                              </td>
                              <td className="px-3 py-3 font-bold capitalize">{token.assigned_role || "user"}</td>
                              <td className="px-3 py-3">
                                <span className={`rounded-full border px-2.5 py-1 text-xs font-bold ${token.status === "available" ? "border-emerald-300 bg-emerald-50 text-emerald-800" : "border-slate-300 bg-slate-50 text-slate-600"}`}>{token.status}</span>
                              </td>
                              <td className="px-3 py-3 font-bold">{token.assigned_role === "student" ? "Không áp dụng" : `${token.exam_count || 0} / ${token.exam_limit}`}</td>
                              <td className="px-3 py-3 font-bold">{token.device_count || 0} / {token.max_devices || 1}</td>
                              <td className="px-3 py-3 text-slate-500">{new Date(token.created_at).toLocaleDateString("vi-VN")}</td>
                              <td className="px-3 py-3 text-right">
                                {(token.status === "available" || token.status === "redeemed") && <button onClick={() => revoke("tokens", token.id)} className="rounded-lg border border-red-300 p-2 text-red-700 hover:bg-red-50" title="Thu hồi"><Ban className="h-4 w-4" /></button>}
                                {token.owner_user_id && <button onClick={() => reissue(token.id)} className="ml-2 rounded-lg border border-[#1f4e79] p-2 text-[#1f4e79] hover:bg-slate-50" title="Cấp lại mã cho máy mới, giữ nguyên dữ liệu"><RefreshCw className="h-4 w-4" /></button>}
                                <button onClick={() => deleteToken(token.id)} className="ml-2 rounded-lg border border-red-300 p-2 text-red-700 hover:bg-red-50" title="Xóa vĩnh viễn token"><Trash2 className="h-4 w-4" /></button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {tokens.length === 0 && <p className="py-10 text-center text-sm text-slate-500">Không tìm thấy token phù hợp.</p>}
                    </div>
                    <div className="mt-3 flex items-center justify-between gap-3 text-sm text-slate-500">
                      <span>{tokenTotal.toLocaleString("vi-VN")} token</span>
                      <div className="flex items-center gap-2">
                        <button type="button" disabled={tokenPage <= 1} onClick={() => void loadTokens(tokenPage - 1)} className="rounded-lg border border-slate-300 px-3 py-1.5 font-bold disabled:opacity-40">Trước</button>
                        <span>Trang {tokenPage}/{tokenPages}</span>
                        <button type="button" disabled={tokenPage >= tokenPages} onClick={() => void loadTokens(tokenPage + 1)} className="rounded-lg border border-slate-300 px-3 py-1.5 font-bold disabled:opacity-40">Sau</button>
                      </div>
                    </div>
                  </div>
                </div>
              ) : tab === "users" ? (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[700px] text-left text-sm">
                    <thead className="text-xs uppercase text-slate-500">
                      <tr>
                        <th className="px-3 py-3">Người dùng</th>
                        <th className="px-3 py-3">Vai trò</th>
                        <th className="px-3 py-3">Đã tạo / Giới hạn</th>
                        <th className="px-3 py-3">Thiết bị</th>
                        <th className="px-3 py-3">Trạng thái</th>
                        <th className="px-3 py-3 text-right">Thao tác</th>
                      </tr>
                    </thead>
                    <tbody>
                      {users.map((u) => (
                        <tr key={u.id} className="border-t border-slate-200">
                          <td className="px-3 py-3">
                            <strong className="block text-slate-900">{u.display_name}</strong>
                            <span className="text-xs text-slate-500">{u.email || "Không có email"}</span>
                          </td>
                          <td className="px-3 py-3">
                            <span
                              className={`rounded-full border px-2 py-0.5 text-xs font-bold ${u.role === "admin"
                                  ? "border-purple-300 bg-purple-50 text-purple-800"
                                  : "border-slate-300 bg-slate-50 text-slate-600"
                                }`}
                            >
                              {u.role}
                            </span>
                          </td>
                          <td className="px-3 py-3 font-bold">
                            {u.role === "student"
                              ? "Không áp dụng"
                              : `${u.exam_count} / ${u.role === "admin" || u.exam_limit == null ? "∞" : u.exam_limit}`}
                          </td>
                          <td className="px-3 py-3">
                            <div className="flex items-center gap-2">
                              <span className="font-bold">
                                {u.device_count} / {u.role === "admin" ? "∞" : (u.device_limit || 1)}
                              </span>
                              {u.role !== "admin" && (
                                <span className="inline-flex overflow-hidden rounded-lg border border-slate-300">
                                  <button
                                    type="button"
                                    onClick={() => void updateDeviceLimit(u.id, (u.device_limit || 1) - 1)}
                                    disabled={(u.device_limit || 1) <= 1 || u.device_count > (u.device_limit || 1) - 1}
                                    className="h-7 w-7 text-sm font-bold text-[#1f4e79] hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-35"
                                    aria-label={`Giảm giới hạn thiết bị của ${u.display_name}`}
                                  >
                                    −
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => void updateDeviceLimit(u.id, (u.device_limit || 1) + 1)}
                                    disabled={(u.device_limit || 1) >= 2}
                                    className="h-7 w-7 border-l border-slate-300 text-sm font-bold text-[#1f4e79] hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-35"
                                    aria-label={`Tăng giới hạn thiết bị của ${u.display_name}`}
                                  >
                                    +
                                  </button>
                                </span>
                              )}
                            </div>
                          </td>
                          <td className="px-3 py-3">
                            <span
                              className={`rounded-full border px-2 py-0.5 text-xs font-bold ${u.status === "active"
                                  ? "border-emerald-300 bg-emerald-50 text-emerald-800"
                                  : "border-red-300 bg-red-50 text-red-800"
                                }`}
                            >
                              {u.status}
                            </span>
                          </td>
                          <td className="px-3 py-3 text-right space-x-1">
                            <button
                              onClick={() =>
                                setUserModal({
                                  open: true,
                                  mode: "edit",
                                  id: u.id,
                                  display_name: u.display_name,
                                  email: u.email || "",
                                  role: u.role,
                                  status: u.status,
                                  exam_limit: u.exam_limit || 5,
                                })
                              }
                              className="rounded-lg border border-slate-300 p-2 text-slate-600 hover:border-[#1f4e79] hover:text-[#1f4e79]"
                              title="Chỉnh sửa"
                            >
                              <Edit className="h-4 w-4" />
                            </button>
                            {u.role !== "admin" && (
                              <button
                                type="button"
                                onClick={() =>
                                  setResetPasswordModal({
                                    open: true,
                                    user_id: u.id,
                                    user_name: u.display_name,
                                    new_password: "",
                                    new_password_confirmation: "",
                                  })
                                }
                                className="rounded-lg border border-amber-300 p-2 text-amber-700 hover:bg-amber-50"
                                title="Đặt lại mật khẩu"
                                aria-label={`Đặt lại mật khẩu cho ${u.display_name}`}
                              >
                                <KeyRound className="h-4 w-4" />
                              </button>
                            )}
                            <button
                              onClick={() => reissueUser(u.id, u.display_name)}
                              className="rounded-lg border border-[#1f4e79] p-2 text-[#1f4e79] hover:bg-slate-50"
                              title="Tạo key chuyển máy"
                            >
                              <RefreshCw className="h-4 w-4" />
                            </button>
                            {u.role !== "admin" && (
                              <button
                                onClick={() => handleDeleteUser(u.id, u.display_name)}
                                className="rounded-lg border border-red-300 p-2 text-red-700 hover:bg-red-50"
                                title="Xóa người dùng"
                              >
                                <Trash2 className="h-4 w-4" />
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : tab === "devices" ? (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[700px] text-left text-sm">
                    <thead className="text-xs uppercase text-slate-500">
                      <tr>
                        <th className="px-3 py-3">Thiết bị</th>
                        <th className="px-3 py-3">Người dùng</th>
                        <th className="px-3 py-3">Kích hoạt</th>
                        <th className="px-3 py-3">Trạng thái</th>
                        <th className="px-3 py-3 text-right">Thao tác</th>
                      </tr>
                    </thead>
                    <tbody>
                      {devices.map((device) => (
                        <tr key={device.id} className="border-t border-slate-200">
                          <td className="px-3 py-3">
                            <strong className="block">{device.name}</strong>
                            <span className="text-xs text-slate-500">{device.platform}</span>
                          </td>
                          <td className="px-3 py-3">{device.user}</td>
                          <td className="px-3 py-3 text-slate-500">
                            {new Date(device.activated_at).toLocaleDateString("vi-VN")}
                          </td>
                          <td className="px-3 py-3">
                            {device.revoked_at ? (
                              <span className="text-xs font-bold text-red-700">Đã thu hồi</span>
                            ) : (
                              <span className="inline-flex items-center gap-1 text-xs font-bold text-emerald-700">
                                <CheckCircle2 className="h-4 w-4" /> Hoạt động
                              </span>
                            )}
                          </td>
                          <td className="px-3 py-3 text-right">
                            {!device.revoked_at && (
                              <button
                                onClick={() => revoke("devices", device.id)}
                                className="rounded-lg border border-red-300 p-2 text-red-700 hover:bg-red-50"
                              >
                                <Ban className="h-4 w-4" />
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="space-y-6">
                  <div className="flex gap-3 border-b border-slate-200 pb-3">
                    <button
                      onClick={() => setPolicyKey("terms")}
                      className={`rounded-lg px-4 py-2 text-xs font-bold ${policyKey === "terms" ? "bg-[#1f4e79] text-white" : "border border-slate-300 bg-white text-slate-600"
                        }`}
                    >
                      <FileText className="mr-1.5 inline h-4 w-4" /> Điều khoản dịch vụ
                    </button>
                    <button
                      onClick={() => setPolicyKey("privacy")}
                      className={`rounded-lg px-4 py-2 text-xs font-bold ${policyKey === "privacy" ? "bg-[#1f4e79] text-white" : "border border-slate-300 bg-white text-slate-600"
                        }`}
                    >
                      <Shield className="mr-1.5 inline h-4 w-4" /> Chính sách bảo mật
                    </button>
                  </div>

                  <div className="space-y-4">
                    <label className="block">
                      <span className="text-sm font-bold text-slate-700">Tiêu đề trang</span>
                      <input
                        value={policyTitle}
                        onChange={(e) => setPolicyTitle(e.target.value)}
                        className="mt-1 w-full rounded-lg border border-slate-300 px-4 py-2 text-sm outline-none focus:border-[#1f4e79]"
                      />
                    </label>
                    <div>
                      <div className="mb-2 flex items-center justify-between gap-3">
                        <span className="text-sm font-bold text-slate-700">Nội dung chính sách</span>
                        <button
                          type="button"
                          onClick={() => setPolicyPreview((current) => !current)}
                          className="ui-btn-secondary px-3 py-1.5 text-xs"
                        >
                          {policyPreview ? "Tiếp tục soạn thảo" : "Xem trước"}
                        </button>
                      </div>
                      {policyPreview ? (
                        <article
                          className="policy-content min-h-72 rounded-lg border border-slate-300 bg-white p-5 text-slate-700"
                          dangerouslySetInnerHTML={{ __html: policyContent }}
                        />
                      ) : (
                        <PolicyEditor value={policyContent} onChange={setPolicyContent} />
                      )}
                    </div>
                    <button
                      onClick={handleSavePolicy}
                      disabled={policySaving}
                      className="ui-btn-primary px-6 py-2.5 text-sm"
                    >
                      {policySaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                      Lưu thay đổi chính sách
                    </button>
                  </div>
                </div>
              )}
            </div>
          </section>
        </div>
      </div>

      {passwordModal.open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-4">
          <div className="w-full max-w-md rounded-2xl border border-slate-300 bg-white p-6 shadow-2xl">
            <h3 className="text-xl font-extrabold text-[#1f4e79]">Đổi mật khẩu admin</h3>
            <p className="mt-1 text-xs text-slate-500">Các phiên admin khác sẽ bị đăng xuất.</p>
            <form onSubmit={handleAdminPasswordChange} className="mt-4 space-y-4">
              <label className="block">
                <span className="text-xs font-bold uppercase text-slate-600">Mật khẩu hiện tại</span>
                <input
                  type="password"
                  value={passwordModal.current_password}
                  onChange={(e) => setPasswordModal({ ...passwordModal, current_password: e.target.value })}
                  minLength={8}
                  maxLength={128}
                  autoComplete="current-password"
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-[#1f4e79]"
                  required
                />
              </label>
              <label className="block">
                <span className="text-xs font-bold uppercase text-slate-600">Mật khẩu mới</span>
                <input
                  type="password"
                  value={passwordModal.new_password}
                  onChange={(e) => setPasswordModal({ ...passwordModal, new_password: e.target.value })}
                  minLength={8}
                  maxLength={128}
                  autoComplete="new-password"
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-[#1f4e79]"
                  required
                />
              </label>
              <label className="block">
                <span className="text-xs font-bold uppercase text-slate-600">Nhập lại mật khẩu mới</span>
                <input
                  type="password"
                  value={passwordModal.new_password_confirmation}
                  onChange={(e) => setPasswordModal({ ...passwordModal, new_password_confirmation: e.target.value })}
                  minLength={8}
                  maxLength={128}
                  autoComplete="new-password"
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-[#1f4e79]"
                  required
                />
              </label>
              <div className="mt-6 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setPasswordModal({ ...passwordModal, open: false })}
                  className="ui-btn-secondary px-4 py-2 text-xs font-bold"
                >
                  Hủy
                </button>
                <button type="submit" className="ui-btn-primary px-4 py-2 text-xs font-bold">
                  Đổi mật khẩu
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {resetPasswordModal.open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-4">
          <div className="w-full max-w-md rounded-2xl border border-slate-300 bg-white p-6 shadow-2xl">
            <h3 className="text-xl font-extrabold text-[#1f4e79]">Đặt lại mật khẩu học viên</h3>
            <p className="mt-1 text-xs text-slate-500">
              Tài khoản: <strong>{resetPasswordModal.user_name}</strong>. Các phiên hiện tại của tài khoản sẽ bị đăng xuất.
            </p>
            <form onSubmit={handleResetUserPassword} className="mt-4 space-y-4">
              <label className="block">
                <span className="text-xs font-bold uppercase text-slate-600">Mật khẩu mới</span>
                <input
                  type="password"
                  value={resetPasswordModal.new_password}
                  onChange={(e) => setResetPasswordModal({ ...resetPasswordModal, new_password: e.target.value })}
                  minLength={8}
                  maxLength={128}
                  autoComplete="new-password"
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-[#1f4e79]"
                  required
                />
              </label>
              <label className="block">
                <span className="text-xs font-bold uppercase text-slate-600">Nhập lại mật khẩu mới</span>
                <input
                  type="password"
                  value={resetPasswordModal.new_password_confirmation}
                  onChange={(e) => setResetPasswordModal({ ...resetPasswordModal, new_password_confirmation: e.target.value })}
                  minLength={8}
                  maxLength={128}
                  autoComplete="new-password"
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-[#1f4e79]"
                  required
                />
              </label>
              <div className="mt-6 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setResetPasswordModal({ ...resetPasswordModal, open: false })}
                  className="ui-btn-secondary px-4 py-2 text-xs font-bold"
                >
                  Hủy
                </button>
                <button type="submit" className="ui-btn-primary px-4 py-2 text-xs font-bold">
                  Đặt lại mật khẩu
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* User Create/Edit Modal */}
      {userModal.open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-4">
          <div className="w-full max-w-md rounded-2xl border border-slate-300 bg-white p-6 shadow-2xl">
            <h3 className="text-xl font-extrabold text-[#1f4e79]">
              {userModal.mode === "create" ? "Thêm người dùng mới" : "Chỉnh sửa người dùng"}
            </h3>
            <form onSubmit={handleSaveUser} className="mt-4 space-y-4">
              <label className="block">
                <span className="text-xs font-bold uppercase text-slate-600">Tên hiển thị</span>
                <input
                  value={userModal.display_name}
                  onChange={(e) => setUserModal({ ...userModal, display_name: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-[#1f4e79]"
                  required
                />
              </label>
              <label className="block">
                <span className="text-xs font-bold uppercase text-slate-600">Email (không bắt buộc)</span>
                <input
                  type="email"
                  value={userModal.email}
                  onChange={(e) => setUserModal({ ...userModal, email: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-[#1f4e79]"
                />
              </label>
              <div className="grid grid-cols-2 gap-3">
                <label className="block">
                  <span className="text-xs font-bold uppercase text-slate-600">Vai trò</span>
                  <select
                    value={userModal.role}
                    onChange={(e) => setUserModal({ ...userModal, role: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-[#1f4e79]"
                  >
                    <option value="user">User</option>
                    <option value="teacher">Teacher</option>
                    <option value="student">Student</option>
                    <option value="admin">Admin</option>
                  </select>
                </label>
                <label className="block">
                  <span className="text-xs font-bold uppercase text-slate-600">Trạng thái</span>
                  <select
                    value={userModal.status}
                    onChange={(e) => setUserModal({ ...userModal, status: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-[#1f4e79]"
                  >
                    <option value="active">Active</option>
                    <option value="disabled">Disabled</option>
                  </select>
                </label>
              </div>
              {!["admin", "student"].includes(userModal.role) && (
                <label className="block">
                  <span className="text-xs font-bold uppercase text-slate-600">
                    Số đề tối đa
                  </span>
                  <input
                    type="number"
                    min={1}
                    max={10000}
                    value={userModal.exam_limit}
                    onChange={(e) =>
                      setUserModal({
                        ...userModal,
                        exam_limit: Number(e.target.value),
                      })
                    }
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-[#1f4e79]"
                    required
                  />
                  <span className="mt-1 block text-xs text-slate-500">
                    Có thể tăng hoặc giảm hạn mức bất kỳ lúc nào.
                  </span>
                </label>
              )}
              <div className="mt-6 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setUserModal({ ...userModal, open: false })}
                  className="ui-btn-secondary px-4 py-2 text-xs font-bold"
                >
                  Hủy
                </button>
                <button type="submit" className="ui-btn-primary px-4 py-2 text-xs font-bold">
                  {userModal.mode === "create" ? "Tạo người dùng" : "Lưu thay đổi"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </main>
  );
}
