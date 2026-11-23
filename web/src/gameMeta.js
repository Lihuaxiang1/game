export const WEAPONS = {
  pistol: { name: '手枪', short: 'PST', trait: '灵活精准', description: '移动速度 +8%，适合游走与精准点射。' },
  rifle: { name: '步枪', short: 'RFL', trait: '自动压制', description: '全自动持续连射，用火力封锁通道。' },
  shotgun: { name: '霰弹枪', short: 'SG', trait: '近身击退', description: '一次 8 枚弹丸，命中可连续击退目标。' },
  sniper: { name: '狙击枪', short: 'SNP', trait: '高伤贯穿', description: '可贯穿首个目标再次命中，移动速度较慢。' },
}

export const PHASES = {
  waiting: '等待准备',
  playing: '对局进行',
  finished: '对局结束',
}
