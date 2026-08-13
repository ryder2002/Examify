"use client";

import { useEffect } from "react";
import { Extension } from "@tiptap/core";
import FontFamily from "@tiptap/extension-font-family";
import Link from "@tiptap/extension-link";
import TextAlign from "@tiptap/extension-text-align";
import TextStyle from "@tiptap/extension-text-style";
import Underline from "@tiptap/extension-underline";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";

const FONT_SIZES = [12, 14, 16, 18, 24, 32];
const FONT_FAMILIES = [
  ["Arial", "Arial"],
  ["Tahoma", "Tahoma"],
  ["Verdana", "Verdana"],
  ["Georgia", "Georgia"],
  ["Times New Roman", "Times New Roman"],
] as const;

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
              attributes.fontSize ? { style: `font-size: ${attributes.fontSize}` } : {},
          },
        },
      },
    ];
  },
});

type Props = {
  value: string;
  onChange: (html: string) => void;
};

function ToolButton({
  active = false,
  label,
  onClick,
}: {
  active?: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded border px-2.5 py-1.5 text-xs font-bold transition ${
        active
          ? "border-[#1f4e79] bg-[#1f4e79] text-white"
          : "border-slate-300 bg-white text-slate-700 hover:border-[#1f4e79]"
      }`}
    >
      {label}
    </button>
  );
}

export default function PolicyEditor({ value, onChange }: Props) {
  const editor = useEditor({
    immediatelyRender: false,
    extensions: [
      StarterKit.configure({ heading: { levels: [1, 2, 3, 4] } }),
      Underline,
      TextStyle,
      FontSize,
      FontFamily.configure({ types: ["textStyle"] }),
      TextAlign.configure({ types: ["heading", "paragraph"] }),
      Link.configure({
        openOnClick: false,
        autolink: true,
        protocols: ["http", "https", "mailto"],
        HTMLAttributes: { target: "_blank", rel: "noopener noreferrer" },
      }),
    ],
    content: value,
    editorProps: {
      attributes: {
        class:
          "min-h-72 rounded-b-lg bg-white p-4 text-sm leading-7 text-slate-800 outline-none [&_a]:text-blue-700 [&_a]:underline [&_blockquote]:border-l-4 [&_blockquote]:border-slate-300 [&_blockquote]:pl-4",
      },
    },
    onUpdate: ({ editor: current }) => onChange(current.getHTML()),
  });

  useEffect(() => {
    if (editor && value !== editor.getHTML()) {
      editor.commands.setContent(value, false);
    }
  }, [editor, value]);

  if (!editor) return null;
  const currentSize = editor.getAttributes("textStyle").fontSize || "";
  const currentFont = editor.getAttributes("textStyle").fontFamily || "";

  const setLink = () => {
    const previous = editor.getAttributes("link").href as string | undefined;
    const href = window.prompt("Liên kết (http, https hoặc mailto):", previous || "");
    if (href === null) return;
    if (!href.trim()) {
      editor.chain().focus().unsetLink().run();
      return;
    }
    editor.chain().focus().setLink({ href: href.trim() }).run();
  };

  return (
    <div className="overflow-hidden rounded-lg border border-slate-300">
      <div className="flex flex-wrap gap-1 border-b border-slate-200 bg-slate-50 p-2">
        <ToolButton active={editor.isActive("bold")} label="B" onClick={() => editor.chain().focus().toggleBold().run()} />
        <ToolButton active={editor.isActive("italic")} label="I" onClick={() => editor.chain().focus().toggleItalic().run()} />
        <ToolButton active={editor.isActive("underline")} label="U" onClick={() => editor.chain().focus().toggleUnderline().run()} />
        <ToolButton active={editor.isActive("heading", { level: 2 })} label="H2" onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()} />
        <ToolButton active={editor.isActive("heading", { level: 3 })} label="H3" onClick={() => editor.chain().focus().toggleHeading({ level: 3 }).run()} />
        <ToolButton active={editor.isActive("bulletList")} label="• List" onClick={() => editor.chain().focus().toggleBulletList().run()} />
        <ToolButton active={editor.isActive("orderedList")} label="1. List" onClick={() => editor.chain().focus().toggleOrderedList().run()} />
        <ToolButton active={editor.isActive("link")} label="Link" onClick={setLink} />
        <ToolButton label="↶" onClick={() => editor.chain().focus().undo().run()} />
        <ToolButton label="↷" onClick={() => editor.chain().focus().redo().run()} />
        <select
          aria-label="Font chữ"
          value={currentFont}
          onChange={(event) => editor.chain().focus().setFontFamily(event.target.value).run()}
          className="rounded border border-slate-300 bg-white px-2 text-xs"
        >
          <option value="">Font chữ</option>
          {FONT_FAMILIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <select
          aria-label="Cỡ chữ"
          value={currentSize}
          onChange={(event) => editor.chain().focus().setMark("textStyle", { fontSize: event.target.value ? `${event.target.value}px` : null }).run()}
          className="rounded border border-slate-300 bg-white px-2 text-xs"
        >
          <option value="">Cỡ chữ</option>
          {FONT_SIZES.map((size) => <option key={size} value={size}>{size}px</option>)}
        </select>
        {(["left", "center", "right"] as const).map((alignment) => (
          <ToolButton
            key={alignment}
            active={editor.isActive({ textAlign: alignment })}
            label={alignment === "left" ? "Trái" : alignment === "center" ? "Giữa" : "Phải"}
            onClick={() => editor.chain().focus().setTextAlign(alignment).run()}
          />
        ))}
      </div>
      <EditorContent editor={editor} />
    </div>
  );
}
