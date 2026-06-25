/**
 * 用 miniprogram-ci 上传小程序代码到微信后台（生成体验版/可提审）。
 *
 * 依赖：npm i miniprogram-ci  （在 weixin_miniprogram 目录下执行）
 * 运行：node scripts/upload.js [版本号] [描述]
 *   例：node scripts/upload.js 1.0.0 "首个版本：近两周速递+问答+账号/管理员"
 *
 * 注意：
 * - 需在「微信公众平台 -> 开发管理 -> 开发设置 -> 小程序代码上传」里
 *   下载/配置代码上传密钥，并把本服务器出口 IP 加入 IP 白名单。
 * - 私钥文件：private.wx9561f446d7eb5180.key（已 .gitignore）。
 */
const path = require('path');
const ci = require('miniprogram-ci');

const APPID = 'wx9561f446d7eb5180';
const PROJECT_ROOT = path.resolve(__dirname, '..');
const KEY_PATH = path.join(PROJECT_ROOT, `private.${APPID}.key`);
const MP_ROOT = path.join(PROJECT_ROOT, 'miniprogram');

const version = process.argv[2] || '1.0.0';
// 备注默认填项目简要说明，而非本次更新内容
const DEFAULT_DESC = '航天速递小程序：近两周国际航天要闻/公众号精选/航天视频/政要社媒 + AI 航天问答';
const desc = process.argv[3] || DEFAULT_DESC;

(async () => {
  const project = new ci.Project({
    appid: APPID,
    type: 'miniProgram',
    projectPath: MP_ROOT,
    privateKeyPath: KEY_PATH,
    ignores: ['node_modules/**/*'],
  });

  const result = await ci.upload({
    project,
    version,
    desc,
    setting: { es6: true, minify: true },
    onProgressUpdate: (t) => {
      const msg = typeof t === 'object' ? (t._msg || JSON.stringify(t)) : t;
      console.log('[upload]', msg);
    },
  });
  console.log('上传完成：', JSON.stringify(result, null, 2));
})().catch((e) => {
  console.error('上传失败：', e && e.message ? e.message : e);
  process.exit(1);
});
