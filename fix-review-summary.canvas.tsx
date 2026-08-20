import { Divider, Grid, H1, H2, Stack, Stat, Table, Text } from 'qoder/canvas';

export default function FixReviewSummary() {
  return (
    <Stack gap={20}>
      <Stack gap={6}>
        <H1>用户修复复核报告</H1>
        <Text tone="secondary">
          对提交 62fcd08(18 文件,+1132/-143)逐项对照审计清单复核 · 只读检查,未代改代码 · 2026-08-20
        </Text>
      </Stack>

      <Grid columns={4} gap={16}>
        <Stat value="253" label="测试全绿(206+47 新增)" />
        <Stat value="4/6" label="确凿 bug 已修复" tone="warning" />
        <Stat value="7/15" label="概率隐患已修复" tone="warning" />
        <Stat value="3" label="遗留必修项" tone="danger" />
      </Grid>

      <Divider />

      <H2>修好且质量高(✅ 逐条回读源码确认)</H2>
      <Table
        headers={['审计项', '修复方式', '证据位置']}
        rows={[
          ['限流 ZADD 同秒覆盖', 'member 加 uuid 唯一后缀,score 语义不变', 'store.py:196'],
          ['enter_session 失败丢事件', 'handle_enter_session 返回 bool + ReportFetchRetryableError 区分可重试/终态 + 失败 msgid_release', 'core.py:287-290'],
          ['SERVICE_HOUR=0 被吞', '_service_hour 正确处理 None/非法值,不再用 or 兜底', 'core.py:497-507'],
          ['欢迎语并发三件套', 'kv_set_nx 原子认领 + 两路径共用标记 + 失败释放 + welcome_code 降级普通发送', 'advisor_report.py:263-300'],
          ['orderflow 乱序回复', '手机号排除正则、渠道长度上限、orderno 重试上限、退出关键词', 'core.py:224、615-628'],
          ['转接前提前删状态', '_merge_flow_context 只读不删,转接成功才 kv_delete', 'core.py:198-202'],
          ['报告 JSON 防护', '顶层 isinstance、gender 白名单、naive 时间处理、nickname 清洗', 'advisor_report.py'],
          ['知识库/links 热加载', '缺失文件缓存防刷屏、OSError/空结果保旧缓存、links 顶层类型校验', 'knowledge.py、links.py:34-37'],
        ]}
      />
      <Text tone="secondary" size="small">
        加分项:wx:event 欢迎语合成 key 去重、DOCTYPE 拒绝、/health 异常返 500、日志 code 清洗、转人工限流豁免。
      </Text>

      <Divider />

      <H2>遗留必修项(❌ 按优先级)</H2>
      <Table
        headers={['优先级', '问题', '证据位置', '一句修法']}
        rows={[
          [
            'P0',
            'POST /callback 仍同步调 _route 阻塞事件循环(每条普通消息都在触发,仅 /wx-callback 包了线程池)',
            'main.py:215-219',
            '与 wx-callback 一致包 run_in_threadpool',
          ],
          [
            'P1',
            '非 ASCII x-internal-key 使 hmac.compare_digest 抛 TypeError 被中间件吞成空 200(两处)',
            'main.py:126、153',
            '比较前 .encode() 或捕获 TypeError 返 401',
          ],
          [
            'P1',
            '_transferred_in_batch 提前标记只修了 orderflow 两处,主转人工路径与 AI 兜底路径漏修;转接失败时用户本批次消息被静默丢弃',
            'core.py:368、411',
            '移到 _handle_kf_transfer 成功分支内',
          ],
        ]}
        rowTone={['danger', 'danger', 'danger']}
      />

      <Divider />

      <H2>仍未处理的隐患(⚠️ 可排入下版本)</H2>
      <Table
        headers={['隐患', '现状']}
        rows={[
          ['XML 解密后二次解析裸调用(core.py:127、131)', 'handler.py 只加了异常契约文档,调用方未 try'],
          ['问卷字段为字符串时逐字符迭代(advisor_report.py:168、173、247)', '报告顶层类型修了,子字段没修'],
          ['Redis 降级跨模式不一致(orderflow/msgid)', '完全未动'],
          ['知识库截断非空内容仍会覆盖缓存', '空结果保旧缓存已修,半成品非空内容未防(mtime+size 双指纹)'],
          ['kf-link 锁跨远程调用无负缓存、body 大小上限、档案 RMW 竞态、硬编码服务时间文案、agent key 不带 kfid', '保持原状,风险可接受'],
        ]}
        rowTone={['warning', 'warning', 'warning', undefined, undefined]}
      />

      <Divider />

      <H2>复核方法</H2>
      <Stack gap={8}>
        <Text>
          1. 全量回归:pytest 253 passed(4.89s),无回归。
        </Text>
        <Text>
          2. 通读 62fcd08 全部 src diff(7 个源文件),对每个审计项逐一定位当前代码。
        </Text>
        <Text>
          3. 关键疑点直接回读源文件验证:/callback 端点、compare_digest 两处、XML 解析调用点、问卷字段迭代、退出关键词使用点。
        </Text>
        <Text>
          4. 复核后确认工作区无新改动,结论对当前代码有效。
        </Text>
      </Stack>

      <Text tone="secondary" size="small">
        结论:修复整体质量不错,6 bug 修 4、15 隐患修约 7;剩余必须处理 P0/P1 三项,其余 P2 可下版本跟进。
      </Text>
    </Stack>
  );
}
