"""Legacy provider parameter defaults and pure default-filling helpers."""

from typing import Optional


PROVIDER_PARAM_TEMPLATES = {
    "openai": {
        "image": {
            "image_size": {
                "enabled": True,
                "default": "1024x1024",
                "options": ["1024x1024", "1792x1024", "1024x1792"]
            },
            "max_reference_images": {
                "enabled": True,
                "default": 8
            }
        }
    },
    "zhangyuge": {
        "image": {
            "image_size": {
                "enabled": True,
                "default": "1:1",
                "options": ["1:1", "16:9", "9:16", "3:2", "2:3", "4:3", "3:4", "5:4", "4:5"]
            },
            "max_reference_images": {"enabled": True, "default": 9}
        },
        "video": {
            "image_size": {
                "enabled": True,
                "default": "16:9",
                "options": ["16:9", "9:16", "1:1"]
            },
            "resolution": {
                "enabled": True,
                "default": "720p",
                "options": ["720p", "1080p"]
            },
            "max_reference_images": {"enabled": True, "default": 9}
        }
    },
    "runway": {
        "video": {
            "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16"]},
            "duration": {"enabled": True, "default": 5, "options": [5, 10, 15]},
            "max_reference_images": {"enabled": True, "default": 9}
        }
    },
    "pika": {
        "video": {
            "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16", "1:1"]},
            "duration": {"enabled": True, "default": 3, "options": [3, 5, 10]},
            "max_reference_images": {"enabled": True, "default": 9}
        }
    },
    "museai-sd": {
        "video": {
            "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16"]},
            "duration": {"enabled": True, "default": 5, "options": [5, 10, 15]},
            "resolution": {"enabled": True, "default": "720p", "options": ["720p", "1080p"]},
            "max_reference_images": {"enabled": True, "default": 9}
        }
    },
    "manxiaobai": {
        "image": {
            "image_size": {"enabled": True, "default": "1:1", "options": ["1:1", "16:9", "9:16", "3:2", "2:3", "4:3", "3:4", "21:9"]},
            "max_reference_images": {"enabled": True, "default": 9}
        },
        "models": {
            # gpt-image-2 系列（官方文档 https://api.manxiaobai.online/about）
            # 档位由模型名后缀锁定：裸名 gpt-image-2 不强制档位尺寸；-1k/-2k/-4k 必须传对应档位尺寸。
            # size 传"比例"时，由 ai_service._call_manxiaobai_image_stream 按档位自动映射：
            #   1K: 1:1=1024x1024, 3:2=1536x1024, 2:3=1024x1536, 16:9=1824x1024, 9:16=1024x1824, 4:3=1360x1024, 3:4=1024x1360, 21:9=2384x1024
            #   2K: 1:1=2048x2048, 3:2=2048x1360, 2:3=1360x2048, 16:9=2048x1152, 9:16=1152x2048, 4:3=2048x1536, 3:4=1536x2048, 21:9=2048x880
            #   4K: 1:1=2880x2880, 3:2=3520x2336, 2:3=2336x3520, 16:9=3840x2160, 9:16=2160x3840, 4:3=3312x2480, 3:4=2480x3312, 21:9=3840x1648
            "gpt-image-2": {
                "image": {
                    "image_size": {"enabled": True, "default": "1:1", "options": ["1:1", "16:9", "9:16", "3:2", "2:3", "4:3", "3:4", "21:9"]},
                    "max_reference_images": {"enabled": True, "default": 9}
                }
            },
            "gpt-image-2-1k": {
                "image": {
                    "image_size": {"enabled": True, "default": "1:1", "options": ["1:1", "16:9", "9:16", "3:2", "2:3", "4:3", "3:4", "21:9"]},
                    "max_reference_images": {"enabled": True, "default": 9}
                }
            },
            "gpt-image-2-2k": {
                "image": {
                    "image_size": {"enabled": True, "default": "1:1", "options": ["1:1", "16:9", "9:16", "3:2", "2:3", "4:3", "3:4", "21:9"]},
                    "max_reference_images": {"enabled": True, "default": 9}
                }
            },
            "gpt-image-2-4k": {
                "image": {
                    "image_size": {"enabled": True, "default": "1:1", "options": ["1:1", "16:9", "9:16", "3:2", "2:3", "4:3", "3:4", "21:9"]},
                    "max_reference_images": {"enabled": True, "default": 9}
                }
            }
        },
        "video": {
            "image_size": {"enabled": True, "default": "16:9", "options": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"]},
            "duration": {"enabled": True, "default": 6, "options": [4, 6, 8, 10, 12, 15]},
            "resolution": {"enabled": True, "default": "720p", "options": ["720p", "480p"]},
            "max_reference_images": {"enabled": True, "default": 9}
        }
    },
    "xai": {
        "video": {
            "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3"]},
            "duration": {"enabled": True, "default": 10, "options": [5, 8, 10, 12, 15]},
            "resolution": {"enabled": True, "default": "720p", "options": ["480p", "720p"]},
            "max_reference_images": {"enabled": True, "default": 9}
        }
    },
    "stability": {
        "image": {
            "image_size": {"enabled": True, "default": "1024x1024", "options": ["1024x1024", "1216x832", "832x1216"]},
            "max_reference_images": {"enabled": True, "default": 9}
        }
    },
    "midjourney": {
        "image": {
            "image_size": {"enabled": True, "default": "1024x1024", "options": ["1024x1024", "1792x1024", "1024x1792", "1216x832", "832x1216"]},
            "max_reference_images": {"enabled": True, "default": 9}
        }
    },
    "museai": {
        "image": {
            "image_size": {"enabled": True, "default": "1024x1024", "options": ["1024x1024", "1792x1024", "1024x1792", "1216x832", "832x1216"]},
            "max_reference_images": {"enabled": True, "default": 9}
        }
    },
    "gpt-image": {
        "image": {
            "image_size": {
                "enabled": True,
                "default": "1024x1024",
                "options": ["1024x1024", "1536x1024", "1024x1536", "2048x2048", "2048x1152", "3840x2160", "2160x3840", "auto"]
            },
            "max_reference_images": {"enabled": True, "default": 9}
        }
    },
    "manxueapi": {
        "image": {
            "image_size": {"enabled": True, "default": "1024x1024", "options": ["1024x1024", "1536x1024", "1024x1536", "2048x2048", "2048x1152", "1152x2048", "3840x2160", "2160x3840"]},
            "quality": {"enabled": True, "default": "high", "options": ["auto", "low", "medium", "high"]},
            "max_reference_images": {"enabled": True, "default": 16}
        },
        "video": {
            "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16", "1:1"]},
            "duration": {"enabled": True, "default": 10, "options": [10, 15]},
            "resolution": {"enabled": True, "default": "720p", "options": ["480p", "720p"]},
            "max_reference_images": {"enabled": True, "default": 9}
        },
        # 模型级参数（覆盖供应商默认值）
        "models": {
            "1ren-dance-2-1": {
                "video": {
                    "image_size": {"enabled": True, "default": "16:9", "options": ["1:1", "16:9", "9:16", "4:3", "3:4"]},
                    "duration": {"enabled": True, "default": 10, "options": [10, 15]},
                    "resolution": {"enabled": True, "default": "720p", "options": ["720p"]},
                    "max_reference_images": {"enabled": True, "default": 9}
                }
            },
            "1ren-dance-2-fast-1": {
                "video": {
                    "image_size": {"enabled": True, "default": "16:9", "options": ["1:1", "3:4", "4:3", "9:16", "16:9", "21:9", "adaptive"]},
                    "duration": {"enabled": True, "default": 10, "options": [10, 15]},
                    "resolution": {"enabled": True, "default": "720p", "options": ["720p"]},
                    "max_reference_images": {"enabled": True, "default": 4}
                }
            },
            "count-dance01-2-pro": {
                "video": {
                    "image_size": {"enabled": True, "default": "16:9", "options": ["1:1", "3:4", "4:3", "9:16", "16:9", "21:9", "adaptive"]},
                    "duration": {"enabled": True, "default": 10, "options": [10, 15]},
                    "resolution": {"enabled": True, "default": "720p", "options": ["720p"]},
                    "max_reference_images": {"enabled": True, "default": 9}
                }
            },
            "1ren-minimax-h3": {
                "video": {
                    "image_size": {"enabled": True, "default": "16:9", "options": ["1:1", "16:9", "9:16"]},
                    "duration": {"enabled": True, "default": 10, "options": [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]},
                    "resolution": {"enabled": True, "default": "720p", "options": ["480p", "720p", "1080p", "2k"]},
                    "max_reference_images": {"enabled": True, "default": 9},
                    "enable_acceleration": {"enabled": True, "default": True}
                }
            }
        }
    },
    "snumom": {
        "image": {
            "image_size": {
                "enabled": True,
                "default": "1024x1024",
                "options": ["1024x1024"]
            },
            "max_reference_images": {
                "enabled": True,
                "default": 7,
                "options": [1, 2, 3, 4, 5, 6, 7]
            }
        },
        "video": {
            "image_size": {
                "enabled": True,
                "default": "16:9",
                "options": ["16:9", "9:16", "1:1"]
            },
            "duration": {
                "enabled": True,
                "default": 6,
                "options": [6, 7, 8, 9, 10, 11, 12, 13, 14, 15]
            },
            "resolution": {
                "enabled": True,
                "default": "720p",
                "options": ["480p", "720p"]
            },
            "max_reference_images": {
                "enabled": True,
                "default": 7,
                "options": [1, 2, 3, 4, 5, 6, 7]
            }
        },
        "models": {
            # 按秒计费模型 grok-video-1.5（0.07 元/秒，隐藏别名 grok-video-1.5）：分辨率目前只用 720p（1080p 会报错）
            "grok-video-1.5-preview": {
                "video": {
                    "resolution": {"enabled": True, "default": "720p", "options": ["720p"]}
                }
            },
            # 按次计费模型 grok-imagine-video-1.5（0.5 元/次）：仅 480p/720p（1080p 入口直接 400）
            "grok-imagine-video-1.5": {
                "video": {
                    "resolution": {"enabled": True, "default": "720p", "options": ["480p", "720p"]}
                }
            },
            # 旧模型（保留兼容；后端已支持 WxH→比例映射，参考图限 1 张）
            "grok-imagine-video": {
                "video": {
                    "duration": {"enabled": True, "default": 6, "options": [6, 7, 8, 9, 10, 11, 12, 13, 14, 15]},
                    "max_reference_images": {"enabled": True, "default": 1}
                }
            },
            "grok-imagine-video-preview": {
                "video": {
                    "duration": {"enabled": True, "default": 6, "options": [6, 7, 8, 9, 10, 11, 12, 13, 14, 15]},
                    "max_reference_images": {"enabled": True, "default": 1}
                }
            },
            "grok-imagine-video-1.5-preview": {
                "video": {
                    "duration": {"enabled": True, "default": 6, "options": [6, 7, 8, 9, 10, 11, 12, 13, 14, 15]},
                    "max_reference_images": {"enabled": True, "default": 1}
                }
            },
            "grok-imagine-image-quality": {
                "image": {
                    "image_size": {"enabled": False},
                    "max_reference_images": {"enabled": False}
                }
            },
            "grok-imagine-image-edit": {}
        }
    },
    "geeknow": {
        "image": {
            "max_reference_images": {"enabled": True, "default": 9},
            "image_size": {
                "enabled": True,
                "default": "1024x1024",
                "options": [
                    # gpt-image-2 基础尺寸
                    "1024x1024", "1536x1152", "1536x1024", "1024x1536",
                    "1920x1080", "1080x1920",
                    # gpt-image-2-pro 2K 尺寸
                    "2048x2048", "2048x1536", "2560x1712", "1712x2560",
                    "2048x1152", "1152x2048",
                    # gpt-image-2-pro 4K 尺寸
                    "2880x2880", "3840x2880", "3840x2560", "2560x3840",
                    "3840x2160", "2160x3840",
                    # gpt-image-2-vip 1K 尺寸
                    "1024x768", "768x1024", "1008x672", "672x1008",
                    "1280x720", "720x1280", "1344x576",
                    # gpt-image-2-vip 2K 尺寸
                    "2304x1728", "1728x2304", "2496x1664", "1664x2496",
                    "2560x1440", "1440x2560", "3024x1296",
                    # gpt-image-2-vip 4K 尺寸
                    "3264x2448", "2448x3264", "3504x2336", "2336x3504",
                    "3808x1632"
                ]
            }
        },
        "models": {
            "gpt-image-2": {
                "image": {
                    "image_size": {
                        "enabled": True,
                        "default": "1024x1024",
                        "options": [
                            "1024x1024", "1536x1152", "1536x1024", "1024x1536",
                            "1920x1080", "1080x1920"
                        ]
                    }
                }
            },
            "gpt-image-2-pro": {
                "image": {
                    "image_size": {
                        "enabled": True,
                        "default": "2048x2048",
                        "options": [
                            "2048x2048", "2048x1536", "2560x1712", "1712x2560",
                            "2048x1152", "1152x2048",
                            "2880x2880", "3840x2880", "3840x2560", "2560x3840",
                            "3840x2160", "2160x3840"
                        ]
                    }
                }
            },
            "gpt-image-2-vip": {
                "image": {
                    "image_size": {
                        "enabled": True,
                        "default": "1024x1024",
                        "options": [
                            "1024x1024", "1024x768", "768x1024",
                            "1008x672", "672x1008", "1280x720", "720x1280",
                            "1344x576", "2048x2048", "2304x1728", "1728x2304",
                            "2496x1664", "1664x2496", "2560x1440", "1440x2560",
                            "3024x1296", "2880x2880", "3264x2448", "2448x3264",
                            "3504x2336", "2336x3504", "3840x2160", "2160x3840",
                            "3808x1632"
                        ]
                    }
                }
            },
            "grok-4-2-image": {
                "image": {
                    "image_size": {"enabled": True, "default": "1024x1024", "options": ["1024x1024", "2560x1440", "1440x2560"]},
                    "max_reference_images": {"enabled": True, "default": 4}
                }
            },
            "gpt-image-1": {
                "image": {
                    "image_size": {"enabled": True, "default": "1024x1024", "options": ["1024x1024", "1792x1024", "1024x1792"]},
                    "max_reference_images": {"enabled": True, "default": 4}
                }
            },
            "dall-e-3": {
                "image": {
                    "image_size": {"enabled": True, "default": "1024x1024", "options": ["1024x1024", "1792x1024", "1024x1792"]}
                }
            },
            "dall-e-2": {
                "image": {
                    "image_size": {"enabled": True, "default": "1024x1024", "options": ["1024x1024", "512x512", "256x256"]}
                }
            },
            # 视频模型（geeknow 视频支持矩阵，统一 POST /v1/videos）
            "grok-imagine-video": {
                "video": {
                    "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3"]},
                    "duration": {"enabled": True, "default": 10, "options": [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]},
                    "resolution": {"enabled": True, "default": "720P", "options": ["480P", "720P"]},
                    "max_reference_images": {"enabled": True, "default": 7, "options": [1, 2, 3, 4, 5, 6, 7]}
                }
            },
            "grok-imagine-video-1.5-preview": {
                "video": {
                    "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3"]},
                    "duration": {"enabled": True, "default": 10, "options": [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]},
                    "resolution": {"enabled": True, "default": "720P", "options": ["480P", "720P"]},
                    "max_reference_images": {"enabled": True, "default": 7, "options": [1, 2, 3, 4, 5, 6, 7]}
                }
            },
            "sora-2": {
                "video": {
                    "image_size": {"enabled": True, "default": "1280x720", "options": ["1280x720", "720x1280", "1024x1024"]},
                    "duration": {"enabled": True, "default": 10, "options": [4, 5, 6, 7, 8, 9, 10]},
                    "max_reference_images": {"enabled": True, "default": 3}
                }
            },
            "veo_3_1": {
                "video": {
                    "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16", "1:1"]},
                    "duration": {"enabled": True, "default": 8, "options": [4, 5, 6, 7, 8, 9, 10]},
                    "max_reference_images": {"enabled": True, "default": 3}
                }
            },
            "veo_3_1-fast": {
                "video": {
                    "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16", "1:1"]},
                    "duration": {"enabled": True, "default": 8, "options": [4, 5, 6, 7, 8, 9, 10]},
                    "max_reference_images": {"enabled": True, "default": 3}
                }
            },
            "omni-fast": {
                "video": {
                    "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16", "1:1"]},
                    "duration": {"enabled": True, "default": 10, "options": [4, 5, 6, 7, 8, 9, 10, 12, 15, 20, 25, 30]},
                    "max_reference_images": {"enabled": True, "default": 5}
                }
            },
            "omni-fast-v2v": {
                "video": {
                    "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16", "1:1"]},
                    "duration": {"enabled": True, "default": 10, "options": [4, 5, 6, 7, 8, 9, 10, 12, 15, 20, 25, 30]},
                    "max_reference_images": {"enabled": True, "default": 5}
                }
            },
            "minimax-h3-768p": {
                "video": {
                    "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16", "1:1", "21:9", "4:3", "3:4"]},
                    "duration": {"enabled": True, "default": 10, "options": [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]},
                    "max_reference_images": {"enabled": True, "default": 5}
                }
            },
            "minimax-h3-2k": {
                "video": {
                    "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16", "1:1", "21:9", "4:3", "3:4"]},
                    "duration": {"enabled": True, "default": 10, "options": [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]},
                    "max_reference_images": {"enabled": True, "default": 5}
                }
            },
            "minimax-h3-pro-768p": {
                "video": {
                    "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16", "1:1", "21:9", "4:3", "3:4"]},
                    "duration": {"enabled": True, "default": 10, "options": [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]},
                    "max_reference_images": {"enabled": True, "default": 9}
                }
            },
            "minimax-h3-pro-2k": {
                "video": {
                    "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16", "1:1", "21:9", "4:3", "3:4"]},
                    "duration": {"enabled": True, "default": 10, "options": [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]},
                    "max_reference_images": {"enabled": True, "default": 9}
                }
            },
            "manxue-2.5": {
                "video": {
                    "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16", "1:1", "21:9"]},
                    "duration": {"enabled": True, "default": 10, "options": [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29]},
                    "resolution": {"enabled": True, "default": "720p", "options": ["480p", "720p"]},
                    "max_reference_images": {"enabled": True, "default": 9}
                }
            },
            "manxue-2.0": {
                "video": {
                    "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16", "1:1"]},
                    "duration": {"enabled": True, "default": 10, "options": [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]},
                    "resolution": {"enabled": True, "default": "720P", "options": ["480P", "720P"]},
                    "max_reference_images": {"enabled": True, "default": 3}
                }
            },
            "doubao-seedance-2-0-260128": {
                "video": {
                    "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16", "1:1"]},
                    "duration": {"enabled": True, "default": 6, "options": [4, 5, 6, 8, 10]},
                    "max_reference_images": {"enabled": True, "default": 7}
                }
            },
            "doubao-seedance-2-0-fast-260128": {
                "video": {
                    "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16", "1:1"]},
                    "duration": {"enabled": True, "default": 6, "options": [4, 5, 6, 8, 10]},
                    "max_reference_images": {"enabled": True, "default": 7}
                }
            },
            "Vidu-q3-pro": {
                "video": {
                    "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16", "1:1"]},
                    "duration": {"enabled": True, "default": 8, "options": [4, 5, 6, 8, 10]},
                    "max_reference_images": {"enabled": True, "default": 3}
                }
            },
            "Vidu-q3-turbo": {
                "video": {
                    "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16", "1:1"]},
                    "duration": {"enabled": True, "default": 8, "options": [4, 5, 6, 8, 10]},
                    "max_reference_images": {"enabled": True, "default": 3}
                }
            },
            "Kling-3.0": {
                "video": {
                    "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16", "1:1"]},
                    "duration": {"enabled": True, "default": 5, "options": [5, 10]},
                    "max_reference_images": {"enabled": True, "default": 1}
                }
            },
            "Kling-3.0-Omni": {
                "video": {
                    "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16", "1:1"]},
                    "duration": {"enabled": True, "default": 5, "options": [5, 10]},
                    "max_reference_images": {"enabled": True, "default": 1}
                }
            },
            "Hailuo-2.3": {
                "video": {
                    "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16", "1:1"]},
                    "duration": {"enabled": True, "default": 6, "options": [4, 5, 6, 8, 10]},
                    "max_reference_images": {"enabled": True, "default": 1}
                }
            },
            "Hailuo-2.3-fast": {
                "video": {
                    "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16", "1:1"]},
                    "duration": {"enabled": True, "default": 6, "options": [4, 5, 6, 8, 10]},
                    "max_reference_images": {"enabled": True, "default": 1}
                }
            }
        },
        "video": {
            "image_size": {"enabled": True, "default": "16:9", "options": ["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3"]},
            "duration": {"enabled": True, "default": 10, "options": [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]},
            "resolution": {"enabled": True, "default": "720P", "options": ["480P", "720P"]},
            "max_reference_images": {"enabled": True, "default": 7, "options": [1, 2, 3, 4, 5, 6, 7]}
        }
    },
    "jiekou": {
        "image": {
            "max_reference_images": {"enabled": True, "default": 9},
            "image_size": {
                "enabled": True,
                "default": "1024x1024",
                "options": [
                    "1024x1024", "1024x1536", "1536x1024",
                    "2048x2048", "2048x1152", "3840x2160", "2160x3840",
                    "2048x1360", "1360x2048", "1152x2048", "2048x1536",
                    "1536x2048", "2048x880", "880x2048",
                    "688x2048", "2048x688", "2048x1024", "1024x2048"
                ]
            },
            "quality": {
                "enabled": True,
                "default": "medium",
                "options": ["low", "medium", "high"]
            }
        }
    },
    "biglongxia": {
        "video": {
            "image_size": {
                "enabled": True,
                "default": "16:9",
                "options": ["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3"]
            },
            "duration": {
                "enabled": True,
                "default": 10,
                "options": [6, 8, 10]
            },
            "max_reference_images": {
                "enabled": True,
                "default": 9
            }
        }
    },
    "heima": {
        "image": {
            # GPT-image-2 / GPT-image-2.5 官方只支持比例（文档「支持size」），分隔符为 x
            "image_size": {
                "enabled": True,
                "default": "16x9",
                "options": ["16x9", "9x16", "4x3", "1x1", "3x4"]
            },
            "max_reference_images": {
                "enabled": True,
                "default": 9
            }
        },
        "video": {
            "image_size": {
                "enabled": True,
                "default": "16:9",
                "options": ["16:9", "9:16", "1:1"]
            },
            "duration": {
                "enabled": True,
                "default": 10,
                "options": [6, 10, 15]
            },
            "max_reference_images": {
                "enabled": True,
                "default": 1
            }
        },
        "models": {
            # 香蕉 Pro：推荐用简化模型名（firefly-nano-banana-pro-2k/4k），用户选尺寸，后台自动拼接完整模型名
            "firefly-nano-banana-pro-2k": {
                "image": {
                    "image_size": {
                        "enabled": True,
                        "default": "16x9",
                        "options": ["16x9", "9x16", "4x3", "1x1", "3x4"]
                    }
                }
            },
            "firefly-nano-banana-pro-4k": {
                "image": {
                    "image_size": {
                        "enabled": True,
                        "default": "16x9",
                        "options": ["16x9", "9x16", "4x3", "1x1", "3x4"]
                    }
                }
            },
            # 兼容已配置完整模型名（firefly-nano-banana-pro-2k-16x9 等）：尺寸已写在模型名上，前端不显示尺寸下拉框
            "firefly-nano-banana-pro-2k-16x9": {
                "image": {
                    "image_size": {"enabled": False}
                }
            },
            "firefly-nano-banana-pro-2k-9x16": {
                "image": {
                    "image_size": {"enabled": False}
                }
            },
            "firefly-nano-banana-pro-2k-4x3": {
                "image": {
                    "image_size": {"enabled": False}
                }
            },
            "firefly-nano-banana-pro-2k-1x1": {
                "image": {
                    "image_size": {"enabled": False}
                }
            },
            "firefly-nano-banana-pro-2k-3x4": {
                "image": {
                    "image_size": {"enabled": False}
                }
            },
            "firefly-nano-banana-pro-4k-16x9": {
                "image": {
                    "image_size": {"enabled": False}
                }
            },
            "firefly-nano-banana-pro-4k-9x16": {
                "image": {
                    "image_size": {"enabled": False}
                }
            },
            "firefly-nano-banana-pro-4k-4x3": {
                "image": {
                    "image_size": {"enabled": False}
                }
            },
            "firefly-nano-banana-pro-4k-1x1": {
                "image": {
                    "image_size": {"enabled": False}
                }
            },
            "firefly-nano-banana-pro-4k-3x4": {
                "image": {
                    "image_size": {"enabled": False}
                }
            },
            # 香蕉 2（nano-banana2）：模型名固定，尺寸通过 size 字段传（格式如 16x9-2k）
            "nano-banana2": {
                "image": {
                    "image_size": {
                        "enabled": True,
                        "default": "16x9-2k",
                        "options": [
                            "1x1-2k", "16x9-2k", "9x16-2k", "4x3-2k", "3x4-2k",
                            "1x1-4k", "16x9-4k", "9x16-4k", "4x3-4k", "3x4-4k"
                        ]
                    }
                }
            },
            # GPT-image-2 / GPT-image-2.5：统一 images/generations 接口（文档 2026-09-13 更新）
            # 模型名固定，尺寸走 size 字段（16x9 等比例），参考图走 images 数组
            "gpt-image-2-1k": {
                "image": {
                    "image_size": {
                        "enabled": True,
                        "default": "16x9",
                        "options": ["16x9", "9x16", "4x3", "1x1", "3x4"]
                    },
                    "quality": {"enabled": True, "default": "high", "options": ["low", "medium", "high"]}
                }
            },
            "gpt-image-2-2k": {
                "image": {
                    "image_size": {
                        "enabled": True,
                        "default": "16x9",
                        "options": ["16x9", "9x16", "4x3", "1x1", "3x4"]
                    },
                    "quality": {"enabled": True, "default": "high", "options": ["low", "medium", "high"]}
                }
            },
            "gpt-image-2-4k": {
                "image": {
                    "image_size": {
                        "enabled": True,
                        "default": "16x9",
                        "options": ["16x9", "9x16", "4x3", "1x1", "3x4"]
                    },
                    "quality": {"enabled": True, "default": "high", "options": ["low", "medium", "high"]}
                }
            },
            "gpt-image-2.5-1k": {
                "image": {
                    "image_size": {
                        "enabled": True,
                        "default": "16x9",
                        "options": ["16x9", "9x16", "4x3", "1x1", "3x4"]
                    },
                    "quality": {"enabled": True, "default": "high", "options": ["low", "medium", "high"]}
                }
            },
            "grok-imagine-video-1.5-preview-6s": {
                "video": {
                    "duration": {"enabled": True, "default": 6, "options": [6]}
                }
            },
            "grok-imagine-video-1.5-preview-10s": {
                "video": {
                    "duration": {"enabled": True, "default": 10, "options": [10]}
                }
            },
            "grok-imagine-video-1.5-preview-15s": {
                "video": {
                    "duration": {"enabled": True, "default": 15, "options": [15]}
                }
            }
        }
    },
    "yu25": {
        "video": {
            "image_size": {
                "enabled": True,
                "default": "16:9",
                "options": ["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3"]
            },
            "duration": {
                "enabled": True,
                "default": 6,
                "options": [6, 10, 15]
            },
            "resolution": {
                "enabled": True,
                "default": "720p",
                "options": ["480p", "720p"]
            },
            "max_reference_images": {
                "enabled": True,
                "default": 7,
                "options": [1, 2, 3, 4, 5, 6, 7]
            }
        }
    },
    "yu25_beiyong": {
        "video": {
            "image_size": {
                "enabled": True,
                "default": "16:9",
                "options": ["16:9", "9:16", "1:1", "2:3", "3:2"]
            },
            "duration": {
                "enabled": True,
                "default": 6,
                "options": [6, 10, 12, 15, 16, 20]
            },
            "resolution": {
                "enabled": True,
                "default": "720p",
                "options": ["480p", "720p", "1080p"]
            },
            "max_reference_images": {
                "enabled": True,
                "default": 7,
                "options": [1, 2, 3, 4, 5, 6, 7]
            }
        },
        "models": {
            "grok-imagine-video": {
                "video": {
                    "duration": {"enabled": True, "default": 6, "options": [6, 10]},
                    "resolution": {"enabled": True, "default": "720p", "options": ["480p", "720p"]}
                }
            },
            "grok-imagine-1.5-video-fast": {
                "video": {
                    "duration": {"enabled": True, "default": 6, "options": [6, 10, 12, 16, 20]},
                    "resolution": {"enabled": True, "default": "720p", "options": ["480p", "720p"]}
                }
            },
            "grok-imagine-video-1.5": {
                "video": {
                    "duration": {"enabled": True, "default": 10, "options": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]},
                    "resolution": {"enabled": True, "default": "720p", "options": ["480p", "720p", "1080p"]},
                    "max_reference_images": {"enabled": True, "default": 1, "options": [1]}
                }
            }
        }
    },
    "haoai": {
        "image": {
            "image_size": {
                "enabled": True,
                "default": "1024x1024",
                "options": ["auto", "1024x1024", "1536x1024", "1024x1536",
                            "2048x2048", "2048x1152", "1152x2048",
                            "3840x2160", "2160x3840"]
            },
            "quality": {
                "enabled": True,
                "default": "auto",
                "options": ["auto", "low", "medium", "high"]
            },
            "max_reference_images": {
                "enabled": True,
                "default": 16
            }
        },
        "video": {
            "image_size": {
                "enabled": True,
                "default": "16:9",
                "options": ["16:9", "9:16", "1:1"]
            },
            "duration": {
                "enabled": True,
                "default": 4,
                "options": [4, 5, 8, 10]
            },
            "resolution": {
                "enabled": True,
                "default": "720p",
                "options": ["720p", "1080p"]
            },
            "max_reference_images": {
                "enabled": True,
                "default": 9
            }
        },
        # 模型级参数（覆盖供应商默认值）
        "models": {
            "grok-imagine-video-1.5-preview": {
                "video": {
                    "duration": {"enabled": True, "default": 4, "options": [4, 5, 8, 10]},
                    "resolution": {"enabled": True, "default": "720p", "options": ["720p", "1080p"]},
                    "max_reference_images": {"enabled": True, "default": 7, "options": [1, 2, 3, 4, 5, 6, 7]}
                }
            }
        }
    },
    "yiyun": {
        "video": {
            "image_size": {
                "enabled": True,
                "default": "16:9",
                "options": ["1:1", "3:4", "4:3", "9:16", "16:9", "21:9"]
            },
            "duration": {
                "enabled": True,
                "default": 5,
                "options": [5, 10, 15]
            },
            "max_reference_images": {
                "enabled": True,
                "default": 9
            }
        }
    },
    "suqing": {
        "video": {
            "image_size": {
                "enabled": True,
                "default": "16:9",
                "options": ["16:9", "9:16", "1:1", "2:3", "3:2"]
            },
            "duration": {
                "enabled": True,
                "default": 6,
                "options": [6, 10]
            },
            "resolution": {
                "enabled": True,
                "default": "HD",
                "options": ["HD", "SD"]
            },
            "max_reference_images": {
                "enabled": True,
                "default": 1
            }
        },
        # 模型级参数（覆盖供应商默认值）
        "models": {
            "grok-imagine-video-1.5-preview": {
                "video": {
                    "duration": {"enabled": True, "default": 6, "options": [6, 10]},
                    "resolution": {"enabled": True, "default": "HD", "options": ["HD", "SD"]},
                    "max_reference_images": {"enabled": True, "default": 1}
                }
            }
        }
    },
}


def _get_default_params(provider: str, category: str, model_name: str = "") -> Optional[str]:
    """根据供应商、分类和模型名获取默认参数。模型级覆盖供应商级。"""
    import json
    provider_lower = provider.lower()
    if provider_lower not in PROVIDER_PARAM_TEMPLATES:
        return None
    template = PROVIDER_PARAM_TEMPLATES[provider_lower]
    if category not in template:
        return None
    # 供应商级参数
    params = {**template[category]}
    # 模型级覆盖
    model_lower = model_name.lower()
    models_override = template.get("models", {}).get(model_lower, {}).get(category)
    if models_override:
        params = {**params, **models_override}
    return json.dumps(params, ensure_ascii=False)


_BILLING_PARAM_KEYS = {"billing_mode", "per_second_cost", "per_second_by_quality", "fixed_by_quality"}


def _fill_default_params(params, provider, category, model_name=""):
    """智能补齐默认参数：
    - params 为空 → 直接用默认模板
    - params 仅含计费字段（billing_mode / per_second_cost 等）→ 以默认模板为基底，保留计费字段，补齐模型参数
    - params 已含其他模型参数 → 以用户配置为准，原样返回
    """
    import json
    current = {}
    if params and params.strip():
        try:
            parsed = json.loads(params)
            if not isinstance(parsed, dict):
                return params  # 非对象，无法补齐，保持原样
            current = parsed
        except (json.JSONDecodeError, TypeError):
            return params  # 无法解析，保持原样
        # 已配置了真实模型参数 → 尊重用户配置
        if any(k not in _BILLING_PARAM_KEYS for k in current):
            return params
    defaults = _get_default_params(provider, category, model_name)
    if not defaults:
        return params if params else None
    try:
        default_dict = json.loads(defaults)
    except json.JSONDecodeError:
        return params
    merged = {**default_dict, **current}
    return json.dumps(merged, ensure_ascii=False)
