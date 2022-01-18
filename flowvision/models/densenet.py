"""
Modified from https://github.com/pytorch/vision/blob/main/torchvision/models/densenet.py
"""
from collections import OrderedDict
from typing import Any, List, Tuple

import oneflow as flow
import oneflow.nn as nn
import oneflow.nn.functional as F

from .utils import load_state_dict_from_url
from .registry import ModelCreator


__all__ = ["DenseNet", "densenet121", "densenet169", "densenet201", "densenet161"]


model_urls = {
    "densenet121": "https://oneflow-public.oss-cn-beijing.aliyuncs.com/model_zoo/flowvision/classification/DenseNet/densenet_121.zip",
    "densenet169": "https://oneflow-public.oss-cn-beijing.aliyuncs.com/model_zoo/flowvision/classification/DenseNet/densenet_169.zip",
    "densenet201": "https://oneflow-public.oss-cn-beijing.aliyuncs.com/model_zoo/flowvision/classification/DenseNet/densenet_201.zip",
    "densenet161": "https://oneflow-public.oss-cn-beijing.aliyuncs.com/model_zoo/flowvision/classification/DenseNet/densenet_161.zip",
}


class _DenseLayer(nn.Module):
    def __init__(
        self, num_input_features: int, growth_rate: int, bn_size: int, drop_rate: float,
    ) -> None:
        super(_DenseLayer, self).__init__()
        self.norm1: nn.BatchNorm2d
        self.add_module("norm1", nn.BatchNorm2d(num_input_features))
        self.relu1: nn.ReLU
        self.add_module("relu1", nn.ReLU(inplace=True))
        self.conv1: nn.Conv2d
        self.add_module(
            "conv1",
            nn.Conv2d(
                num_input_features,
                bn_size * growth_rate,
                kernel_size=1,
                stride=1,
                bias=False,
            ),
        )
        self.norm2: nn.BatchNorm2d
        self.add_module("norm2", nn.BatchNorm2d(bn_size * growth_rate))
        self.relu2: nn.ReLU
        self.add_module("relu2", nn.ReLU(inplace=True))
        self.conv2: nn.Conv2d
        self.add_module(
            "conv2",
            nn.Conv2d(
                bn_size * growth_rate,
                growth_rate,
                kernel_size=3,
                stride=1,
                padding=1,
                bias=False,
            ),
        )
        self.drop_rate = float(drop_rate)

    def bn_function(self, inputs: List[flow.Tensor]) -> flow.Tensor:
        concated_features = flow.cat(inputs, 1)
        bottleneck_output = self.conv1(
            self.relu1(self.norm1(concated_features))
        )  # noqa: T484
        return bottleneck_output

    # todo: rewrite when torchscript supports any
    def any_requires_grad(self, input: List[flow.Tensor]) -> bool:
        for tensor in input:
            if tensor.requires_grad:
                return True
        return False

    def forward(self, input: List[flow.Tensor]) -> flow.Tensor:
        pass

    def forward(self, input: flow.Tensor) -> flow.Tensor:
        pass

    def forward(self, input) -> flow.Tensor:  # noqa: F811
        if isinstance(input, flow.Tensor):
            prev_features = [input]
        else:
            prev_features = input

        bottleneck_output = self.bn_function(prev_features)

        new_features = self.conv2(self.relu2(self.norm2(bottleneck_output)))
        if self.drop_rate > 0:
            new_features = F.dropout(
                new_features, p=self.drop_rate, training=self.training
            )
        return new_features


class _DenseBlock(nn.ModuleDict):
    _version = 2

    def __init__(
        self,
        num_layers: int,
        num_input_features: int,
        bn_size: int,
        growth_rate: int,
        drop_rate: float,
    ) -> None:
        super(_DenseBlock, self).__init__()
        for i in range(num_layers):
            layer = _DenseLayer(
                num_input_features + i * growth_rate,
                growth_rate=growth_rate,
                bn_size=bn_size,
                drop_rate=drop_rate,
            )
            self.add_module("denselayer%d" % (i + 1), layer)

    def forward(self, init_features):
        features = [init_features]
        for name, layer in self.items():
            new_features = layer(features)
            features.append(new_features)
        return flow.cat(features, dim=1)


class _Transition(nn.Sequential):
    def __init__(self, num_input_features: int, num_output_features: int) -> None:
        super(_Transition, self).__init__()
        self.add_module("norm", nn.BatchNorm2d(num_input_features))
        self.add_module("relu", nn.ReLU(inplace=True))
        self.add_module(
            "conv",
            nn.Conv2d(
                num_input_features,
                num_output_features,
                kernel_size=1,
                stride=1,
                bias=False,
            ),
        )
        self.add_module("pool", nn.AvgPool2d(kernel_size=2, stride=2))


class DenseNet(nn.Module):
    r"""Densenet-BC model class, based on
    `"Densely Connected Convolutional Networks" <https://arxiv.org/pdf/1608.06993.pdf>`_.
    Args:
        growth_rate (int): How many filters to add each layer (`k` in paper)
        block_config (list of 4 ints): How many layers in each pooling block
        num_init_features (int): The number of filters to learn in the first convolution layer
        bn_size (int): Multiplicative factor for number of bottle neck layers
          (i.e. bn_size * k features in the bottleneck layer)
        drop_rate (float): Dropout rate after each dense layer
        num_classes (int): Number of classification classes
    """

    def __init__(
        self,
        growth_rate: int = 32,
        block_config: Tuple[int, int, int, int] = (6, 12, 24, 16),
        num_init_features: int = 64,
        bn_size: int = 4,
        drop_rate: float = 0,
        num_classes: int = 1000,
    ) -> None:

        super(DenseNet, self).__init__()

        # First convolution
        self.features = nn.Sequential(
            OrderedDict(
                [
                    (
                        "conv0",
                        nn.Conv2d(
                            3,
                            num_init_features,
                            kernel_size=7,
                            stride=2,
                            padding=3,
                            bias=False,
                        ),
                    ),
                    ("norm0", nn.BatchNorm2d(num_init_features)),
                    ("relu0", nn.ReLU(inplace=True)),
                    ("pool0", nn.MaxPool2d(kernel_size=3, stride=2, padding=1)),
                ]
            )
        )

        # Each denseblock
        num_features = num_init_features
        for i, num_layers in enumerate(block_config):
            block = _DenseBlock(
                num_layers=num_layers,
                num_input_features=num_features,
                bn_size=bn_size,
                growth_rate=growth_rate,
                drop_rate=drop_rate,
            )
            self.features.add_module("denseblock%d" % (i + 1), block)
            num_features = num_features + num_layers * growth_rate
            if i != len(block_config) - 1:
                trans = _Transition(
                    num_input_features=num_features,
                    num_output_features=num_features // 2,
                )
                self.features.add_module("transition%d" % (i + 1), trans)
                num_features = num_features // 2

        # Final batch norm
        self.features.add_module("norm5", nn.BatchNorm2d(num_features))

        # Linear layer
        self.classifier = nn.Linear(num_features, num_classes)

        # Official init from torch repo.
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight)
            elif isinstance(m, nn.BatchNorm2d):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)
            elif isinstance(m, nn.Linear):
                nn.init.constant_(m.bias, 0)

    def forward(self, x: flow.Tensor) -> flow.Tensor:
        features = self.features(x)
        out = F.relu(features, inplace=True)
        out = F.adaptive_avg_pool2d(out, (1, 1))
        out = flow.flatten(out, 1)
        out = self.classifier(out)
        return out


def _load_pretrained(
    model_name: str,
    model: nn.Module,
    progress: bool,
    model_dir: str = "./checkpoints",
    check_hash: bool = False,
) -> None:
    if model_name not in model_urls or model_urls[model_name] is None:
        raise ValueError(
            "No checkpoint is available for model type {}".format(model_name)
        )
    checkpoint_url = model_urls[model_name]
    model.load_state_dict(
        load_state_dict_from_url(
            checkpoint_url, model_dir, progress=progress, check_hash=check_hash
        )
    )


def _densenet(
    arch: str,
    growth_rate: int,
    block_config: Tuple[int, int, int, int],
    num_init_features: int,
    pretrained: bool,
    progress: bool,
    **kwargs: Any
) -> DenseNet:
    model = DenseNet(growth_rate, block_config, num_init_features, **kwargs)
    if pretrained:
        _load_pretrained(arch, model, progress)
    return model


@ModelCreator.register_model
def densenet121(
    pretrained: bool = False, progress: bool = True, **kwargs: Any
) -> DenseNet:
    """
    Constructs the DenseNet-121 model.

    .. note::
        DenseNet-121 model architecture from the `Densely Connected Convolutional Networks <https://arxiv.org/pdf/1608.06993.pdf>`_ paper.
        The required minimum input size of the model is 29x29.

    Args:
        pretrained (bool): Whether to download the pre-trained model on ImageNet. Default: ``False``
        progress (bool): If True, displays a progress bar of the download to stderr. Default: ``True``

    For example:

    .. code-block:: python

        >>> import flowvision
        >>> densenet121 = flowvision.models.densenet121(pretrained=False, progress=True)

    """
    return _densenet(
        "densenet121", 32, (6, 12, 24, 16), 64, pretrained, progress, **kwargs
    )


@ModelCreator.register_model
def densenet161(
    pretrained: bool = False, progress: bool = True, **kwargs: Any
) -> DenseNet:
    """
    Constructs the DenseNet-161 model.

    .. note::
        DenseNet-161 model architecture from the `Densely Connected Convolutional Networks <https://arxiv.org/pdf/1608.06993.pdf>`_ paper.
        The required minimum input size of the model is 29x29.

    Args:
        pretrained (bool): Whether to download the pre-trained model on ImageNet. Default: ``False``
        progress (bool): If True, displays a progress bar of the download to stderr. Default: ``True``

    For example:

    .. code-block:: python

        >>> import flowvision
        >>> densenet161 = flowvision.models.densenet161(pretrained=False, progress=True)

    """
    return _densenet(
        "densenet161", 48, (6, 12, 36, 24), 96, pretrained, progress, **kwargs
    )


@ModelCreator.register_model
def densenet169(
    pretrained: bool = False, progress: bool = True, **kwargs: Any
) -> DenseNet:
    """
    Constructs the DenseNet-169 model.

    .. note::
        DenseNet-169 model architecture from the `Densely Connected Convolutional Networks <https://arxiv.org/pdf/1608.06993.pdf>`_ paper.
        The required minimum input size of the model is 29x29.

    Args:
        pretrained (bool): Whether to download the pre-trained model on ImageNet. Default: ``False``
        progress (bool): If True, displays a progress bar of the download to stderr. Default: ``True``

    For example:

    .. code-block:: python

        >>> import flowvision
        >>> densenet169 = flowvision.models.densenet169(pretrained=False, progress=True)

    """
    return _densenet(
        "densenet169", 32, (6, 12, 32, 32), 64, pretrained, progress, **kwargs
    )


@ModelCreator.register_model
def densenet201(
    pretrained: bool = False, progress: bool = True, **kwargs: Any
) -> DenseNet:
    """
    Constructs the DenseNet-201 model.

    .. note::
        DenseNet-201 model architecture from the `Densely Connected Convolutional Networks <https://arxiv.org/pdf/1608.06993.pdf>`_ paper.
        The required minimum input size of the model is 29x29.

    Args:
        pretrained (bool): Whether to download the pre-trained model on ImageNet. Default: ``False``
        progress (bool): If True, displays a progress bar of the download to stderr. Default: ``True``

    For example:

    .. code-block:: python

        >>> import flowvision
        >>> densenet201 = flowvision.models.densenet201(pretrained=False, progress=True)

    """
    return _densenet(
        "densenet201", 32, (6, 12, 48, 32), 64, pretrained, progress, **kwargs
    )
