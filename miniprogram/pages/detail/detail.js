const api = require('../../utils/api');

const MD_HEADING = /^\s{0,3}(#{1,6})\s*(.+?)\s*#*\s*$/i;
const SEG_LINE = /^[#@\s]*SEG\d+(?:[#@\s]*SEG\d+)*[#@\s]*$/i;
const TITLE_TAG = /^#([^\s#]+)\s+/;

function stripTitleTags(title) {
  let t = String(title || '').trim();
  while (TITLE_TAG.test(t)) t = t.replace(TITLE_TAG, '').trim();
  return t;
}

function cleanBodyMarkers(text) {
  if (!text) return '';
  const lines = String(text).replace(/\r\n/g, '\n').split('\n');
  const out = [];
  for (const line of lines) {
    const raw = line.trim();
    if (!raw) {
      out.push('');
      continue;
    }
    if (SEG_LINE.test(raw)) continue;
    let cleaned = line.replace(
      /(?:#{0,6}\s*)?(?:@@@)?SEG\d+(?:(?:\s*|@{0,3})SEG\d+)*(?:@@@)?(?:\s*#{0,6})?/gi,
      ''
    );
    if (/^[#@\s]*$/.test(cleaned || '')) continue;
    out.push(cleaned.replace(/\s+$/, ''));
  }
  return out.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

/** 拆成 [{type:'h'|'p', text}]，识别 ##/###/#### 小节标题，去掉 SEG 噪声 */
function toBlocks(body) {
  if (!body) return [];
  let t = String(body).replace(/<[^>]+>/g, '\n');
  t = t.replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&')
       .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"');
  t = cleanBodyMarkers(t);
  const blocks = [];
  const paras = t.split(/\n{2,}/).map((s) => s.trim()).filter(Boolean);
  for (const p of paras) {
    const lines = p.split('\n');
    if (lines.length === 1) {
      const m = lines[0].match(MD_HEADING);
      if (m && m[2] && m[2].length <= 60 && !/SEG\d+/i.test(m[2])) {
        blocks.push({ type: 'h', text: m[2].trim() });
        continue;
      }
    }
    let buf = [];
    let sawHeading = false;
    for (const ln of lines) {
      const m = ln.trim().match(MD_HEADING);
      if (m && m[2] && m[2].length <= 60 && !/SEG\d+/i.test(m[2])) {
        if (buf.length) {
          blocks.push({ type: 'p', text: buf.join('\n').trim() });
          buf = [];
        }
        blocks.push({ type: 'h', text: m[2].trim() });
        sawHeading = true;
      } else {
        buf.push(ln);
      }
    }
    if (buf.length) {
      blocks.push({ type: 'p', text: buf.join('\n').trim() });
    } else if (!sawHeading && p) {
      blocks.push({ type: 'p', text: p });
    }
  }
  return blocks.filter((b) => b.text);
}

function toParagraphs(body) {
  return toBlocks(body).filter((b) => b.type === 'p').map((b) => b.text);
}

Page({
  data: {
    item: null,
    paragraphs: [],
    blocks: [],
    loading: true,
    error: '',
  },

  onLoad(query) {
    const id = query.id;
    this._shareQuery = { id: id || '', topic: query.topic || '' };
    wx.showShareMenu({ menus: ['shareAppMessage', 'shareTimeline'] });
    if (!id) {
      this.setData({ loading: false, error: '缺少参数' });
      return;
    }
    const path = query.topic
      ? '/topic/item?topic=' + query.topic + '&id=' + id
      : '/news/item?id=' + id;
    api.get(path, { auth: false })
      .then((res) => {
        const item = res.item;
        if (item && !Array.isArray(item.images)) item.images = [];
        if (item && item.title) item.title = stripTitleTags(item.title);
        if (item && item.summary_zh) {
          item.summary_zh = cleanBodyMarkers(stripTitleTags(item.summary_zh));
        }
        // 技术港/每日发射：正文只取「描述」，结构化字段单独成卡片，避免与卡片重复
        let bodySrc = item.body;
        if (item.kind === 'techport') bodySrc = item.tp_summary || item.body;
        else if (item.kind === 'launch') bodySrc = item.launch_summary || '';
        else if (item.kind === 'future') bodySrc = '';
        const blocks = toBlocks(bodySrc);
        this.setData({
          item,
          blocks,
          paragraphs: blocks.filter((b) => b.type === 'p').map((b) => b.text),
          loading: false,
        });
        wx.setNavigationBarTitle({ title: item.main_tag || '详情' });
      })
      .catch((e) => this.setData({ loading: false, error: e.message || '加载失败' }));
  },

  onShareAppMessage() {
    const item = this.data.item || {};
    const query = this._shareQuery || {};
    let path = '/pages/detail/detail?id=' + encodeURIComponent(query.id || item.id || '');
    if (query.topic) path += '&topic=' + encodeURIComponent(query.topic);
    return {
      title: item.title || '航天速递',
      path,
      imageUrl: item.image || '',
    };
  },

  onShareTimeline() {
    const item = this.data.item || {};
    const query = this._shareQuery || {};
    let params = 'id=' + encodeURIComponent(query.id || item.id || '');
    if (query.topic) params += '&topic=' + encodeURIComponent(query.topic);
    return {
      title: item.title || '航天速递',
      query: params,
      imageUrl: item.image || '',
    };
  },

  copyLink() {
    const link = this.data.item && this.data.item.link;
    if (!link) return;
    wx.setClipboardData({ data: link, success: () => wx.showToast({ title: '链接已复制', icon: 'none' }) });
  },

  copyShare() {
    const txt = this.data.item && this.data.item.share_text;
    if (!txt) return;
    wx.setClipboardData({ data: txt, success: () => wx.showToast({ title: '口令已复制，去抖音搜索粘贴', icon: 'none' }) });
  },

  copyTitle() {
    const t = this.data.item && this.data.item.title;
    if (!t) return;
    wx.setClipboardData({ data: t, success: () => wx.showToast({ title: '标题已复制，去微信搜索粘贴', icon: 'none' }) });
  },
});
