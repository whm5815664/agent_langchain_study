const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  HeadingLevel, AlignmentType, WidthType, ShadingType, LevelFormat, BorderStyle
} = require('docx');
const fs = require('fs');
const path = require('path');

const border = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const borders = { top: border, bottom: border, left: border, right: border };
const COLS = [1800, 3200, 1400, 2960]; // sum = 9360 (US Letter content width)

function cell(text, opts = {}) {
  const { bold = false, fill = null, center = false, width } = opts;
  return new TableCell({
    borders,
    width: { size: width, type: WidthType.DXA },
    shading: fill ? { fill, type: ShadingType.CLEAR } : undefined,
    margins: { top: 60, bottom: 60, left: 80, right: 80 },
    children: [
      new Paragraph({
        alignment: center ? AlignmentType.CENTER : AlignmentType.LEFT,
        children: [new TextRun({ text: String(text ?? ""), bold, size: 18, font: "Arial" })],
      }),
    ],
  });
}

function createParameterTable(params) {
  const header = new TableRow({
    children: [
      cell("参数名", { bold: true, fill: "E0E0E0", center: true, width: COLS[0] }),
      cell("类型", { bold: true, fill: "E0E0E0", center: true, width: COLS[1] }),
      cell("默认值", { bold: true, fill: "E0E0E0", center: true, width: COLS[2] }),
      cell("说明", { bold: true, fill: "E0E0E0", center: true, width: COLS[3] }),
    ],
  });
  const rows = params.map(
    (p) =>
      new TableRow({
        children: [
          cell(p.name, { bold: true, width: COLS[0] }),
          cell(p.type, { width: COLS[1] }),
          cell(p.default, { width: COLS[2] }),
          cell(p.desc, { width: COLS[3] }),
        ],
      })
  );
  return new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: COLS,
    rows: [header, ...rows],
  });
}

function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 320, after: 160 },
    children: [new TextRun({ text, bold: true, size: 28, font: "Arial" })],
  });
}

function p(text, opts = {}) {
  return new Paragraph({
    spacing: { before: opts.before || 0, after: opts.after || 120 },
    alignment: opts.align,
    children: [new TextRun({ text, bold: opts.bold, size: 22, font: "Arial" })],
  });
}

function bullet(text) {
  return new Paragraph({
    numbering: { reference: "bullets", level: 0 },
    spacing: { after: 80 },
    children: [new TextRun({ text, size: 22, font: "Arial" })],
  });
}

function numbered(text, ref) {
  return new Paragraph({
    numbering: { reference: ref, level: 0 },
    spacing: { after: 40 },
    children: [new TextRun({ text, size: 22, font: "Arial" })],
  });
}

const doc = new Document({
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
  },
  numbering: {
    config: [
      {
        reference: "bullets",
        levels: [
          {
            level: 0,
            format: LevelFormat.BULLET,
            text: "•",
            alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 720, hanging: 360 } } },
          },
        ],
      },
      {
        reference: "base-stack",
        levels: [
          {
            level: 0,
            format: LevelFormat.DECIMAL,
            text: "%1.",
            alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 720, hanging: 360 } } },
          },
        ],
      },
      {
        reference: "tail-stack",
        levels: [
          {
            level: 0,
            format: LevelFormat.DECIMAL,
            text: "%1.",
            alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 720, hanging: 360 } } },
          },
        ],
      },
    ],
  },
  sections: [
    {
      properties: {
        page: {
          size: { width: 12240, height: 15840 },
          margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
        },
      },
      children: [
        new Paragraph({
          heading: HeadingLevel.TITLE,
          alignment: AlignmentType.CENTER,
          spacing: { after: 400 },
          children: [new TextRun({ text: "create_deep_agent 参数说明", bold: true, size: 36, font: "Arial" })],
        }),
        p("本文档介绍 LangChain DeepAgents 框架中 create_deep_agent 函数的主要参数。", { after: 200 }),

        h1("一、核心参数"),
        createParameterTable([
          {
            name: "model",
            type: "str | BaseChatModel | None",
            default: "None",
            desc: "要使用的模型。支持 provider:model 格式，或传入预初始化的 BaseChatModel 实例。",
          },
          {
            name: "tools",
            type: "Sequence[BaseTool | Callable | dict]",
            default: "None",
            desc: "额外工具列表，与内置工具（write_todos、文件系统、execute、task）合并。",
          },
          {
            name: "system_prompt",
            type: "str | SystemMessage | None",
            default: "None",
            desc: "自定义系统提示，放在系统提示开头。",
          },
        ]),

        h1("二、中间件与子代理"),
        createParameterTable([
          {
            name: "middleware",
            type: "Sequence[AgentMiddleware]",
            default: "()",
            desc: "插入在基础中间件栈之后、尾部中间件之前。",
          },
          {
            name: "subagents",
            type: "Sequence[SubAgent | CompiledSubAgent | AsyncSubAgent]",
            default: "None",
            desc: "子代理配置。未提供 general-purpose 时会自动添加默认通用子代理。",
          },
        ]),

        h1("三、技能与记忆"),
        createParameterTable([
          {
            name: "skills",
            type: "list[str] | None",
            default: "None",
            desc: "技能源路径列表（如 [/skills/user/]），相对后端根目录。",
          },
          {
            name: "memory",
            type: "list[str] | None",
            default: "None",
            desc: "要加载的记忆文件路径列表（AGENTS.md）。",
          },
        ]),

        h1("四、权限与后端"),
        createParameterTable([
          {
            name: "permissions",
            type: "list[FilesystemPermission]",
            default: "None",
            desc: "文件系统权限规则；mode 可为 allow / deny / interrupt。",
          },
          {
            name: "backend",
            type: "BackendProtocol | Callable | None",
            default: "None",
            desc: "文件存储与执行后端，如 StateBackend / FilesystemBackend。",
          },
        ]),

        h1("五、其他常用参数"),
        createParameterTable([
          {
            name: "checkpointer",
            type: "None | bool | BaseCheckpointSaver",
            default: "None",
            desc: "在运行之间持久化代理状态。",
          },
          {
            name: "interrupt_on",
            type: "dict[str, bool | InterruptOnConfig]",
            default: "None",
            desc: "指定工具调用时暂停等待人工批准。",
          },
          {
            name: "name",
            type: "str | None",
            default: "None",
            desc: "代理名称。",
          },
          {
            name: "debug",
            type: "bool",
            default: "False",
            desc: "是否启用调试模式。",
          },
        ]),

        h1("六、内置工具"),
        p("默认情况下，create_deep_agent 创建的代理拥有以下内置工具："),
        bullet("write_todos：管理待办事项列表"),
        bullet("ls / read_file / write_file / edit_file / glob / grep：文件操作"),
        bullet("execute：运行 shell 命令（需 SandboxBackendProtocol）"),
        bullet("task：调用子代理"),

        h1("七、中间件栈顺序"),
        p("基础栈：", { bold: true, after: 80 }),
        numbered("TodoListMiddleware", "base-stack"),
        numbered("SkillsMiddleware（如果提供了 skills）", "base-stack"),
        numbered("FilesystemMiddleware", "base-stack"),
        numbered("SubAgentMiddleware（如果有同步子代理）", "base-stack"),
        numbered("SummarizationMiddleware", "base-stack"),
        numbered("PatchToolCallsMiddleware", "base-stack"),
        numbered("AsyncSubAgentMiddleware（如果有异步子代理）", "base-stack"),

        p("尾部栈：", { bold: true, before: 160, after: 80 }),
        numbered("HarnessProfile.extra_middleware（如果有）", "tail-stack"),
        numbered("_ToolExclusionMiddleware（如果有 excluded_tools）", "tail-stack"),
        numbered("AnthropicPromptCachingMiddleware", "tail-stack"),
        numbered("BedrockPromptCachingMiddleware（如果安装了 langchain-aws）", "tail-stack"),
        numbered("MemoryMiddleware（如果提供了 memory）", "tail-stack"),
        numbered("HumanInTheLoopMiddleware（如果提供了 interrupt_on）", "tail-stack"),

        new Paragraph({
          alignment: AlignmentType.RIGHT,
          spacing: { before: 400 },
          children: [
            new TextRun({
              text: "文档生成时间：" + new Date().toISOString().split("T")[0],
              size: 18,
              font: "Arial",
              italics: true,
            }),
          ],
        }),
      ],
    },
  ],
});

const outPath = path.join(
  process.env.WORK_DIR || __dirname,
  "deep_agent_params.docx"
);

Packer.toBuffer(doc)
  .then((buffer) => {
    fs.writeFileSync(outPath, buffer);
    console.log("文档创建成功:", outPath);
  })
  .catch((err) => {
    console.error("创建文档失败:", err);
    process.exit(1);
  });
