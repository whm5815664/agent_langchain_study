const PptxGenJS = require('pptxgenjs');
const fs = require('fs');
const path = require('path');

// 创建演示文稿
const pptx = new PptxGenJS();

// 设置幻灯片尺寸（16:9）
pptx.layout = 'LAYOUT_16x9';

// 配色方案：Ocean Gradient
const colors = {
  deepBlue: '065A82',
  teal: '1C7293',
  midnight: '21295C',
  white: 'FFFFFF',
  lightBlue: 'CADCFC'
};

// 添加标题页
const slide = pptx.addSlide();

// 背景色
slide.background = { color: colors.deepBlue };

// 标题
slide.addText('LangChain 使用方法', {
  x: 0.5,
  y: 0.5,
  w: 9,
  h: 1,
  fontSize: 40,
  color: colors.white,
  bold: true,
  align: 'left',
  fontFace: 'Arial Black'
});

// 副标题/分隔线
slide.addShape(pptx.ShapeType.rect, {
  x: 0.5,
  y: 1.3,
  w: 2,
  h: 0.15,
  fill: { color: colors.teal }
});

// 主要内容区域 - 使用卡片式布局
const contentY = 1.8;
const cardHeight = 1.1;
const cardWidth = 4.3;
const gap = 0.3;

// 卡片样式函数
function addCard(slide, title, items, x, y) {
  // 卡片背景
  slide.addShape(pptx.ShapeType.rect, {
    x: x,
    y: y,
    w: cardWidth,
    h: cardHeight,
    fill: { color: colors.white },
    rectRadius: 0.2
  });
  
  // 标题背景条
  slide.addShape(pptx.ShapeType.rect, {
    x: x,
    y: y,
    w: cardWidth,
    h: 0.4,
    fill: { color: colors.teal },
    rectRadius: 0.2
  });
  
  // 卡片标题
  slide.addText(title, {
    x: x + 0.3,
    y: y + 0.08,
    w: cardWidth - 0.6,
    h: 0.3,
    fontSize: 16,
    color: colors.white,
    bold: true,
    align: 'left',
    fontFace: 'Georgia'
  });
  
  // 内容项
  let itemY = y + 0.45;
  items.forEach((item, index) => {
    slide.addText(item, {
      x: x + 0.3,
      y: itemY + (index * 0.22),
      w: cardWidth - 0.6,
      h: 0.2,
      fontSize: 12,
      color: '333333',
      align: 'left',
      fontFace: 'Calibri'
    });
  });
}

// 第一行卡片
addCard(slide, '1. 安装与导入', [
  'pip install langchain',
  'from langchain import ...'
], 0.5, contentY);

addCard(slide, '2. 核心组件', [
  '• Models: LLM、ChatModel',
  '• Prompts: 模板管理',
  '• Chains: 组合工作流'
], 0.5 + cardWidth + gap, contentY);

// 第二行卡片
addCard(slide, '3. 构建 Chain', [
  'chain = prompt | model | parser',
  'result = chain.invoke(input)'
], 0.5, contentY + cardHeight + gap);

addCard(slide, '4. 高级功能', [
  '• Memory: 对话记忆',
  '• Agents: 自主决策',
  '• RAG: 检索增强生成'
], 0.5 + cardWidth + gap, contentY + cardHeight + gap);

// 底部代码示例区域
const codeY = contentY + (cardHeight + gap) * 2 + 0.3;
slide.addShape(pptx.ShapeType.rect, {
  x: 0.5,
  y: codeY,
  w: 9,
  h: 1.5,
  fill: { color: colors.midnight },
  rectRadius: 0.15
});

slide.addText('快速示例:', {
  x: 0.7,
  y: codeY + 0.15,
  w: 8.6,
  h: 0.3,
  fontSize: 14,
  color: colors.lightBlue,
  bold: true,
  align: 'left',
  fontFace: 'Consolas'
});

slide.addText(
  `from langchain.prompts import ChatPromptTemplate\n` +
  `from langchain.chat_models import ChatOpenAI\n\n` +
  `prompt = ChatPromptTemplate.from_template("解释{topic}")\n` +
  `model = ChatOpenAI(model="gpt-4")\n` +
  `chain = prompt | model\n` +
  `response = chain.invoke({"topic": "量子计算"})`,
  {
    x: 0.7,
    y: codeY + 0.5,
    w: 8.6,
    h: 1.0,
    fontSize: 11,
    color: colors.white,
    align: 'left',
    fontFace: 'Consolas'
  }
);

// 保存文件
const outputPath = process.env.OUTPUT_DIR || './outputs';
pptx.writeFile({ fileName: path.join(outputPath, 'LangChain 使用方法.pptx') })
  .then((filePath) => {
    console.log('PPT 生成成功:', filePath);
  })
  .catch((err) => {
    console.error('生成失败:', err);
  });
