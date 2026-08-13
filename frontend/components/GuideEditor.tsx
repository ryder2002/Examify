"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Extension, mergeAttributes, Node, type JSONContent } from "@tiptap/core";
import Color from "@tiptap/extension-color";
import FontFamily from "@tiptap/extension-font-family";
import Highlight from "@tiptap/extension-highlight";
import Link from "@tiptap/extension-link";
import Placeholder from "@tiptap/extension-placeholder";
import Subscript from "@tiptap/extension-subscript";
import Superscript from "@tiptap/extension-superscript";
import Table from "@tiptap/extension-table";
import TableCell from "@tiptap/extension-table-cell";
import TableHeader from "@tiptap/extension-table-header";
import TableRow from "@tiptap/extension-table-row";
import TextAlign from "@tiptap/extension-text-align";
import TextStyle from "@tiptap/extension-text-style";
import Underline from "@tiptap/extension-underline";
import {
  EditorContent,
  NodeViewWrapper,
  ReactNodeViewRenderer,
  useEditor,
  type NodeViewProps,
} from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import {
  AlignCenter, AlignJustify, AlignLeft, AlignRight, Bold, Braces,
  ChevronDown, Code, Copy, Eraser, Highlighter, ImageIcon, Indent,
  Italic, Link2, List, ListOrdered, Loader2, Minus, Outdent, Palette, ClipboardPaste,
  PanelTop, Pilcrow, Quote, Redo2, Strikethrough, SubscriptIcon, Unlink,
  SuperscriptIcon, TableIcon, Trash2, UnderlineIcon, Undo2, Video,
} from "lucide-react";

import { apiFetch, guideMediaUrl } from "@/lib/api";
import type { GuideMedia } from "@/lib/guides";

const FONT_FAMILIES = ["Arial", "Times New Roman", "Roboto", "Inter", "Tahoma", "Verdana"];
const FONT_SIZES = [12, 14, 16, 18, 20, 24, 28, 32, 40, 48];

const FontSize = Extension.create({
  name: "fontSize",
  addGlobalAttributes() {
    return [
      {
        types: ["textStyle"],
        attributes: {
          fontSize: {
            default: null,
            parseHTML: (element) => element.style.fontSize || null,
            renderHTML: (attributes) =>
              attributes.fontSize ? { style: `font-size:${attributes.fontSize}` } : {},
          },
        },
      },
    ];
  },
});

function ImageView({ node, updateAttributes, deleteNode, selected }: NodeViewProps) {
  const [replacing, setReplacing] = useState(false);
  const replaceImage = async (file: File) => {
    setReplacing(true);
    try {
      const body = new FormData();
      body.append("file", file);
      const response = await apiFetch("/api/v1/admin/guide-media/upload", { method: "POST", body });
      const media = await response.json();
      if (!response.ok || media.media_type !== "image") {
        throw new Error(media.detail || "Không thay được ảnh");
      }
      updateAttributes({
        src: media.url,
        alt: media.original_name,
        objectKey: media.object_key,
        bucket: media.bucket,
      });
    } catch (reason) {
      window.alert(reason instanceof Error ? reason.message : "Không thay được ảnh");
    } finally {
      setReplacing(false);
    }
  };
  const startResize = (event: React.PointerEvent) => {
    event.preventDefault();
    const figure = (event.currentTarget as HTMLElement).parentElement;
    const container = figure?.parentElement;
    if (!figure || !container) return;
    const startX = event.clientX;
    const startWidth = figure.getBoundingClientRect().width;
    const containerWidth = container.getBoundingClientRect().width;
    const onMove = (move: PointerEvent) => {
      const width = Math.max(20, Math.min(100, ((startWidth + move.clientX - startX) / containerWidth) * 100));
      updateAttributes({ width: `${Math.round(width)}%` });
    };
    const stop = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", stop);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", stop);
  };
  const align = node.attrs.align || "center";
  return (
    <NodeViewWrapper
      as="figure"
      data-guide-image=""
      className={`guide-editor-image relative my-5 ${selected ? "ring-2 ring-blue-500" : ""}`}
      style={{
        width: node.attrs.width || "75%",
        marginLeft: align === "center" || align === "right" ? "auto" : undefined,
        marginRight: align === "center" || align === "left" ? "auto" : undefined,
      }}
    >
      {/* Media is user-authored content, so Next Image optimization is intentionally not used. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={guideMediaUrl(node.attrs.src)}
        alt={node.attrs.alt || ""}
        className="block h-auto max-w-full rounded-lg"
        draggable={false}
      />
      {selected && (
        <div contentEditable={false} className="absolute right-2 top-2 flex flex-wrap gap-1 rounded-lg bg-white/95 p-1 shadow">
          {["25%", "50%", "75%", "100%"].map((width) => (
            <button key={width} type="button" title={`Rộng ${width}`} onClick={() => updateAttributes({ width })} className="rounded px-1.5 py-1 text-[10px] font-bold hover:bg-slate-100">{width}</button>
          ))}
          {(["left", "center", "right"] as const).map((value) => (
            <button key={value} type="button" title={`Căn ${value}`} onClick={() => updateAttributes({ align: value })} className="rounded px-1.5 py-1 text-[10px] font-bold hover:bg-slate-100">{value[0].toUpperCase()}</button>
          ))}
          <label title="Thay ảnh" className="cursor-pointer rounded px-1.5 py-1 text-[10px] font-bold hover:bg-slate-100">
            {replacing ? "..." : "Thay"}
            <input type="file" accept="image/jpeg,image/png,image/webp,image/gif" hidden disabled={replacing} onChange={(event) => event.target.files?.[0] && void replaceImage(event.target.files[0])} />
          </label>
          <button type="button" title="Xóa ảnh" onClick={deleteNode} className="rounded p-1 text-red-600 hover:bg-red-50"><Trash2 className="h-3.5 w-3.5" /></button>
        </div>
      )}
      <button
        type="button"
        contentEditable={false}
        title="Kéo để đổi kích thước"
        onPointerDown={startResize}
        className="absolute bottom-7 right-0 h-4 w-4 cursor-nwse-resize rounded-sm border-2 border-white bg-blue-600 shadow"
      />
      <input
        contentEditable={false}
        value={node.attrs.caption || ""}
        onChange={(event) => updateAttributes({ caption: event.target.value })}
        placeholder="Thêm chú thích ảnh..."
        className="mt-2 w-full border-0 bg-transparent text-center text-xs italic text-slate-500 outline-none"
      />
      {selected && (
        <input
          contentEditable={false}
          value={node.attrs.alt || ""}
          onChange={(event) => updateAttributes({ alt: event.target.value })}
          placeholder="Văn bản thay thế (alt text)..."
          className="mt-1 w-full rounded border border-slate-200 bg-white px-2 py-1 text-xs text-slate-600 outline-none focus:border-blue-500"
        />
      )}
    </NodeViewWrapper>
  );
}

const GuideImage = Node.create({
  name: "guideImage",
  group: "block",
  atom: true,
  draggable: true,
  addAttributes() {
    return {
      src: { default: "" },
      alt: { default: "" },
      caption: { default: "" },
      objectKey: { default: "", parseHTML: (element) => element.querySelector("img")?.getAttribute("data-object-key") || element.getAttribute("data-object-key") || "" },
      bucket: { default: "", parseHTML: (element) => element.querySelector("img")?.getAttribute("data-bucket") || element.getAttribute("data-bucket") || "" },
      width: { default: "75%" },
      align: { default: "center" },
    };
  },
  parseHTML() {
    return [{ tag: "figure[data-guide-image]" }, { tag: "img[data-object-key]" }];
  },
  renderHTML({ HTMLAttributes }) {
    const { caption, objectKey, bucket, align, ...image } = HTMLAttributes;
    const style = `width:${image.width || "75%"};max-width:100%;margin-left:${align === "left" ? "0" : "auto"};margin-right:${align === "right" ? "0" : "auto"}`;
    return [
      "figure",
      { "data-guide-image": "", style },
      ["img", mergeAttributes(image, { "data-object-key": objectKey, "data-bucket": bucket, style: "max-width:100%;height:auto" })],
      ["figcaption", {}, caption || ""],
    ];
  },
  addNodeView() {
    return ReactNodeViewRenderer(ImageView);
  },
});

const GuideVideo = Node.create({
  name: "guideVideo",
  group: "block",
  atom: true,
  draggable: true,
  addAttributes() {
    return {
      src: { default: "" },
      provider: { default: "html5" },
      title: { default: "Video hướng dẫn" },
      objectKey: { default: "" },
      bucket: { default: "" },
    };
  },
  parseHTML() {
    return [{ tag: "video" }, { tag: "iframe" }];
  },
  renderHTML({ HTMLAttributes }) {
    const { provider, objectKey, bucket, ...attributes } = HTMLAttributes;
    if (provider === "youtube") {
      return ["div", { class: "guide-video" }, ["iframe", mergeAttributes(attributes, { loading: "lazy", allowfullscreen: "true", referrerpolicy: "strict-origin-when-cross-origin" })]];
    }
    return ["div", { class: "guide-video" }, ["video", mergeAttributes(attributes, { controls: "true", preload: "metadata", "data-object-key": objectKey, "data-bucket": bucket })]];
  },
});

function ToolButton({
  title, active = false, disabled = false, onClick, children,
}: {
  title: string; active?: boolean; disabled?: boolean; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <button type="button" title={title} aria-label={title} disabled={disabled} onClick={onClick}
      className={`rounded-md border p-1.5 transition disabled:opacity-30 ${active ? "border-[#1f4e79] bg-[#1f4e79] text-white" : "border-slate-300 bg-white text-slate-700 hover:border-[#1f4e79]"}`}>
      {children}
    </button>
  );
}

function youtubeEmbed(value: string): string | null {
  try {
    const url = new URL(value);
    let id = "";
    if (url.hostname === "youtu.be") id = url.pathname.slice(1);
    if (url.hostname.endsWith("youtube.com")) id = url.searchParams.get("v") || url.pathname.split("/").pop() || "";
    return /^[\w-]{6,20}$/.test(id) ? `https://www.youtube-nocookie.com/embed/${id}` : null;
  } catch {
    return null;
  }
}

function MediaLibrary({ onPick, onClose }: { onPick: (media: GuideMedia) => void; onClose: () => void }) {
  const [items, setItems] = useState<GuideMedia[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const timer = window.setTimeout(async () => {
      setLoading(true);
      const response = await apiFetch(`/api/v1/admin/guide-media?q=${encodeURIComponent(query)}`);
      if (response.ok) setItems((await response.json()).items || []);
      setLoading(false);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  const remove = async (media: GuideMedia) => {
    if (!window.confirm(`Xóa file “${media.original_name}”?`)) return;
    const response = await apiFetch(`/api/v1/admin/guide-media/${media.id}`, { method: "DELETE" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) return setError(data.detail || "Không xóa được media");
    setItems((current) => current.filter((item) => item.id !== media.id));
  };
  return (
    <div className="fixed inset-0 z-[110] flex items-center justify-center bg-slate-950/60 p-4" role="dialog" aria-modal="true">
      <div className="max-h-[85vh] w-full max-w-5xl overflow-auto rounded-2xl bg-white p-5 shadow-2xl">
        <div className="flex items-center justify-between gap-4">
          <div><h2 className="text-xl font-extrabold text-[#1f4e79]">Thư viện media</h2><p className="text-xs text-slate-500">Chọn ảnh hoặc video đã tải lên.</p></div>
          <button type="button" onClick={onClose} className="ui-btn-secondary px-3 py-2">Đóng</button>
        </div>
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Tìm theo tên file..." className="mt-4 w-full rounded-lg border border-slate-300 px-4 py-2 outline-none focus:border-[#1f4e79]" />
        {error && <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs font-bold text-red-700">{error}</div>}
        {loading ? <div className="flex min-h-48 items-center justify-center"><Loader2 className="h-6 w-6 animate-spin" /></div> : (
          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            {items.map((media) => (
              <div key={media.id} className="overflow-hidden rounded-xl border border-slate-200 text-left transition hover:border-[#1f4e79]">
                <button type="button" onClick={() => onPick(media)} title="Chèn vào bài viết" className="flex aspect-video w-full items-center justify-center bg-slate-100">
                  {media.media_type === "image" ? <img src={guideMediaUrl(media.url)} alt="" className="h-full w-full object-cover" /> : <Video className="h-8 w-8 text-slate-400" />}
                </button>
                <div className="p-2">
                  <p className="truncate text-xs font-semibold" title={media.original_name}>{media.original_name}</p>
                  <p className="mt-0.5 text-[10px] text-slate-400">{(media.size / 1024 / 1024).toFixed(2)} MB · {new Date(media.created_at).toLocaleDateString("vi-VN")}</p>
                  <div className="mt-2 flex gap-1">
                    <button type="button" onClick={() => onPick(media)} className="flex-1 rounded bg-[#1f4e79] px-2 py-1 text-[10px] font-bold text-white">Chèn</button>
                    <button type="button" title="Sao chép URL" onClick={() => navigator.clipboard.writeText(guideMediaUrl(media.url))} className="rounded border border-slate-300 p-1"><Copy className="h-3 w-3" /></button>
                    <button type="button" title="Xóa file" onClick={() => void remove(media)} className="rounded border border-red-200 p-1 text-red-600"><Trash2 className="h-3 w-3" /></button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function GuideEditor({
  value,
  onChange,
}: {
  value: Record<string, unknown>;
  onChange: (json: Record<string, unknown>, html: string) => void;
}) {
  const uploadRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const editor = useEditor({
    immediatelyRender: false,
    extensions: [
      StarterKit.configure({ heading: { levels: [1, 2, 3, 4] } }),
      Underline, TextStyle, FontSize, FontFamily, Color,
      Highlight.configure({ multicolor: true }), Subscript, Superscript,
      TextAlign.configure({ types: ["heading", "paragraph"] }),
      Link.configure({ openOnClick: false, autolink: true, protocols: ["http", "https", "mailto"], HTMLAttributes: { target: "_blank", rel: "noopener noreferrer" } }),
      Table.configure({ resizable: true }), TableRow, TableHeader, TableCell,
      Placeholder.configure({ placeholder: "Bắt đầu viết nội dung hướng dẫn..." }),
      GuideImage, GuideVideo,
    ],
    content: value as JSONContent,
    editorProps: {
      attributes: { class: "guide-editor-content min-h-[620px] bg-white px-6 py-8 text-slate-800 outline-none sm:px-12" },
      handlePaste: (_view, event) => {
        const image = Array.from(event.clipboardData?.files || []).find((file) => file.type.startsWith("image/"));
        if (image) {
          event.preventDefault();
          void upload(image);
          return true;
        }
        return false;
      },
      handleDrop: (_view, event) => {
        const media = Array.from(event.dataTransfer?.files || []).find((file) => file.type.startsWith("image/") || file.type.startsWith("video/"));
        if (media) {
          event.preventDefault();
          void upload(media);
          return true;
        }
        return false;
      },
    },
    onUpdate: ({ editor: current }) => onChange(current.getJSON() as Record<string, unknown>, current.getHTML()),
  });

  useEffect(() => {
    if (!editor) return;
    if (JSON.stringify(value) !== JSON.stringify(editor.getJSON())) {
      editor.commands.setContent(value as JSONContent, false);
    }
  }, [editor, value]);

  const insertMedia = useCallback((media: GuideMedia) => {
    if (!editor) return;
    if (media.media_type === "image") {
      editor.chain().focus().insertContent({
        type: "guideImage",
        attrs: {
          src: media.url, alt: media.original_name, caption: "",
          objectKey: media.object_key, bucket: media.bucket, width: "75%", align: "center",
        },
      }).run();
    } else {
      editor.chain().focus().insertContent({
        type: "guideVideo",
        attrs: { src: media.url, provider: "html5", title: media.original_name, objectKey: media.object_key, bucket: media.bucket },
      }).run();
    }
    setLibraryOpen(false);
  }, [editor]);

  async function upload(file: File) {
    setUploading(true);
    setError(null);
    try {
      const body = new FormData();
      body.append("file", file);
      const response = await apiFetch("/api/v1/admin/guide-media/upload", { method: "POST", body });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "Không upload được media");
      insertMedia(payload as GuideMedia);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không upload được media");
    } finally {
      setUploading(false);
      if (uploadRef.current) uploadRef.current.value = "";
    }
  }

  if (!editor) return <div className="min-h-80 animate-pulse rounded-xl bg-slate-100" />;
  const setLink = () => {
    const previous = editor.getAttributes("link").href || "";
    const value = window.prompt("Nhập URL http(s) hoặc mailto:", previous);
    if (value === null) return;
    if (!value) return void editor.chain().focus().unsetLink().run();
    if (!/^(https?:\/\/|mailto:)/i.test(value)) return void setError("URL không hợp lệ.");
    editor.chain().focus().setLink({ href: value }).run();
  };
  const addVideoUrl = () => {
    const value = window.prompt("YouTube URL hoặc URL video https:");
    if (!value) return;
    const youtube = youtubeEmbed(value);
    if (youtube) {
      editor.chain().focus().insertContent({ type: "guideVideo", attrs: { src: youtube, provider: "youtube", title: "YouTube video" } }).run();
    } else if (/^https:\/\//i.test(value)) {
      editor.chain().focus().insertContent({ type: "guideVideo", attrs: { src: value, provider: "html5", title: "Video hướng dẫn" } }).run();
    } else setError("URL video không hợp lệ.");
  };
  const command = (callback: () => void) => callback();

  return (
    <div className="overflow-hidden rounded-xl border border-slate-300 bg-slate-100 shadow-sm">
      <div className="sticky top-0 z-20 flex flex-wrap items-center gap-1.5 border-b border-slate-300 bg-slate-50/95 p-2 backdrop-blur">
        <ToolButton title="Chữ đậm" active={editor.isActive("bold")} onClick={() => command(() => editor.chain().focus().toggleBold().run())}><Bold className="h-4 w-4" /></ToolButton>
        <ToolButton title="Chữ nghiêng" active={editor.isActive("italic")} onClick={() => command(() => editor.chain().focus().toggleItalic().run())}><Italic className="h-4 w-4" /></ToolButton>
        <ToolButton title="Gạch chân" active={editor.isActive("underline")} onClick={() => command(() => editor.chain().focus().toggleUnderline().run())}><UnderlineIcon className="h-4 w-4" /></ToolButton>
        <ToolButton title="Gạch ngang" active={editor.isActive("strike")} onClick={() => command(() => editor.chain().focus().toggleStrike().run())}><Strikethrough className="h-4 w-4" /></ToolButton>
        <label title="Màu chữ" className="relative flex h-[30px] w-[30px] cursor-pointer items-center justify-center rounded-md border border-slate-300 bg-white"><Palette className="h-4 w-4" /><input type="color" className="absolute inset-0 opacity-0" onChange={(event) => editor.chain().focus().setColor(event.target.value).run()} /></label>
        <label title="Màu nền chữ" className="relative flex h-[30px] w-[30px] cursor-pointer items-center justify-center rounded-md border border-slate-300 bg-white"><Highlighter className="h-4 w-4" /><input type="color" className="absolute inset-0 opacity-0" onChange={(event) => editor.chain().focus().toggleHighlight({ color: event.target.value }).run()} /></label>
        <ToolButton title="Chỉ số dưới" active={editor.isActive("subscript")} onClick={() => command(() => editor.chain().focus().toggleSubscript().run())}><SubscriptIcon className="h-4 w-4" /></ToolButton>
        <ToolButton title="Chỉ số trên" active={editor.isActive("superscript")} onClick={() => command(() => editor.chain().focus().toggleSuperscript().run())}><SuperscriptIcon className="h-4 w-4" /></ToolButton>
        <span className="mx-0.5 h-6 w-px bg-slate-300" />
        <select title="Kiểu đoạn" value={editor.isActive("heading", { level: 1 }) ? "1" : editor.isActive("heading", { level: 2 }) ? "2" : editor.isActive("heading", { level: 3 }) ? "3" : editor.isActive("heading", { level: 4 }) ? "4" : "p"} onChange={(event) => event.target.value === "p" ? editor.chain().focus().setParagraph().run() : editor.chain().focus().setHeading({ level: Number(event.target.value) as 1 | 2 | 3 | 4 }).run()} className="h-[30px] rounded-md border border-slate-300 bg-white px-2 text-xs">
          <option value="p">Đoạn văn</option><option value="1">Heading 1</option><option value="2">Heading 2</option><option value="3">Heading 3</option><option value="4">Heading 4</option>
        </select>
        <select title="Font chữ" value={editor.getAttributes("textStyle").fontFamily || ""} onChange={(event) => event.target.value ? editor.chain().focus().setFontFamily(event.target.value).run() : editor.chain().focus().unsetFontFamily().run()} className="h-[30px] max-w-32 rounded-md border border-slate-300 bg-white px-2 text-xs"><option value="">Font chữ</option>{FONT_FAMILIES.map((font) => <option key={font}>{font}</option>)}</select>
        <select title="Cỡ chữ" value={(editor.getAttributes("textStyle").fontSize || "").replace("px", "")} onChange={(event) => editor.chain().focus().setMark("textStyle", { fontSize: event.target.value ? `${event.target.value}px` : null }).run()} className="h-[30px] rounded-md border border-slate-300 bg-white px-2 text-xs"><option value="">Cỡ</option>{FONT_SIZES.map((size) => <option key={size}>{size}</option>)}</select>
        <span className="mx-0.5 h-6 w-px bg-slate-300" />
        {([["left", AlignLeft], ["center", AlignCenter], ["right", AlignRight], ["justify", AlignJustify]] as const).map(([align, Icon]) => <ToolButton key={align} title={`Căn ${align}`} active={editor.isActive({ textAlign: align })} onClick={() => command(() => editor.chain().focus().setTextAlign(align).run())}><Icon className="h-4 w-4" /></ToolButton>)}
        <ToolButton title="Danh sách dấu đầu dòng" active={editor.isActive("bulletList")} onClick={() => command(() => editor.chain().focus().toggleBulletList().run())}><List className="h-4 w-4" /></ToolButton>
        <ToolButton title="Danh sách đánh số" active={editor.isActive("orderedList")} onClick={() => command(() => editor.chain().focus().toggleOrderedList().run())}><ListOrdered className="h-4 w-4" /></ToolButton>
        <ToolButton title="Giảm lề danh sách" onClick={() => command(() => editor.chain().focus().liftListItem("listItem").run())}><Outdent className="h-4 w-4" /></ToolButton>
        <ToolButton title="Tăng lề danh sách" onClick={() => command(() => editor.chain().focus().sinkListItem("listItem").run())}><Indent className="h-4 w-4" /></ToolButton>
        <ToolButton title="Trích dẫn" active={editor.isActive("blockquote")} onClick={() => command(() => editor.chain().focus().toggleBlockquote().run())}><Quote className="h-4 w-4" /></ToolButton>
        <ToolButton title="Code inline" active={editor.isActive("code")} onClick={() => command(() => editor.chain().focus().toggleCode().run())}><Code className="h-4 w-4" /></ToolButton>
        <ToolButton title="Khối code" active={editor.isActive("codeBlock")} onClick={() => command(() => editor.chain().focus().toggleCodeBlock().run())}><Braces className="h-4 w-4" /></ToolButton>
        <ToolButton title="Đường kẻ ngang" onClick={() => command(() => editor.chain().focus().setHorizontalRule().run())}><Minus className="h-4 w-4" /></ToolButton>
        <ToolButton title="Thêm hoặc sửa liên kết" active={editor.isActive("link")} onClick={setLink}><Link2 className="h-4 w-4" /></ToolButton>
        <ToolButton title="Xóa liên kết" disabled={!editor.isActive("link")} onClick={() => editor.chain().focus().unsetLink().run()}><Unlink className="h-4 w-4" /></ToolButton>
        <ToolButton title="Chèn bảng 3x3" onClick={() => command(() => editor.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run())}><TableIcon className="h-4 w-4" /></ToolButton>
        <ToolButton title="Thêm hàng bảng" disabled={!editor.can().addRowAfter()} onClick={() => command(() => editor.chain().focus().addRowAfter().run())}><PanelTop className="h-4 w-4" /></ToolButton>
        <ToolButton title="Xóa bảng" disabled={!editor.can().deleteTable()} onClick={() => command(() => editor.chain().focus().deleteTable().run())}><Trash2 className="h-4 w-4" /></ToolButton>
        <input ref={uploadRef} type="file" accept="image/jpeg,image/png,image/webp,image/gif,video/mp4,video/webm,video/quicktime" hidden onChange={(event) => event.target.files?.[0] && void upload(event.target.files[0])} />
        <ToolButton title="Upload ảnh hoặc video" disabled={uploading} onClick={() => uploadRef.current?.click()}>{uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <ImageIcon className="h-4 w-4" />}</ToolButton>
        <ToolButton title="Mở thư viện media" onClick={() => setLibraryOpen(true)}><ChevronDown className="h-4 w-4" /></ToolButton>
        <ToolButton title="Chèn YouTube hoặc video URL" onClick={addVideoUrl}><Video className="h-4 w-4" /></ToolButton>
        <ToolButton title="Hoàn tác" disabled={!editor.can().undo()} onClick={() => command(() => editor.chain().focus().undo().run())}><Undo2 className="h-4 w-4" /></ToolButton>
        <ToolButton title="Làm lại" disabled={!editor.can().redo()} onClick={() => command(() => editor.chain().focus().redo().run())}><Redo2 className="h-4 w-4" /></ToolButton>
        <ToolButton title="Sao chép vùng chọn" onClick={() => document.execCommand("copy")}><Copy className="h-4 w-4" /></ToolButton>
        <ToolButton title="Dán văn bản từ clipboard" onClick={() => void navigator.clipboard.readText().then((text) => editor.view.dispatch(editor.state.tr.insertText(text))).catch(() => setError("Không đọc được clipboard. Hãy dùng Ctrl+V."))}><ClipboardPaste className="h-4 w-4" /></ToolButton>
        <ToolButton title="Chọn toàn bộ" onClick={() => editor.commands.selectAll()}><Pilcrow className="h-4 w-4" /></ToolButton>
        <ToolButton title="Xóa định dạng" onClick={() => command(() => editor.chain().focus().unsetAllMarks().clearNodes().run())}><Eraser className="h-4 w-4" /></ToolButton>
        <ToolButton title="Xóa toàn bộ nội dung" onClick={() => window.confirm("Xóa toàn bộ nội dung?") && editor.commands.clearContent()}><Trash2 className="h-4 w-4" /></ToolButton>
      </div>
      {error && <div className="border-b border-red-200 bg-red-50 px-4 py-2 text-xs font-semibold text-red-700">{error}</div>}
      <div className="mx-auto my-5 max-w-[900px] overflow-hidden rounded-sm border border-slate-200 bg-white shadow-lg">
        <EditorContent editor={editor} />
      </div>
      {libraryOpen && <MediaLibrary onPick={insertMedia} onClose={() => setLibraryOpen(false)} />}
    </div>
  );
}
